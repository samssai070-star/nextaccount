"""
NextAccount v2 — bot/slack_handler.py
Slack Bolt アプリのイベントハンドラを定義する。

対応イベント:
  - file_shared   : 領収書アップロード → OCR → 承認カード表示
  - approve_expense: 承認ボタン → DB更新 + Sheets同期
  - reject_expense : 却下ボタン → DB更新
  - app_mention   : ヘルプ表示

Slack ユーザー名を「申請者（社員名）」として使用する。
"""

from __future__ import annotations

import os
import logging
import requests as http_requests
from datetime import datetime

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from core.config import SLACK_BOT_TOKEN, SLACK_APP_TOKEN, GOOGLE_SHEET_ID
import os
APPROVAL_CHANNEL_ID = os.environ.get("APPROVAL_CHANNEL_ID", "")
from core.ocr import parse_receipt
from core.accounting import build_journal_entry, generate_event_id
from core.database import get_tenant_by_slack_team
from core import (
    init_database,
    init_users_table,
    get_next_sequence,
    check_duplicate,
    insert_event,
    get_event_by_id,
    update_status,
    SheetsManager,
)
from core.database import (
    get_user_by_slack_id,
    upsert_user,
    update_commute_section,
)

logger = logging.getLogger(__name__)

# ============================================================
# テナント解決ヘルパー
# ============================================================

def _get_tenant(team_id: str) -> dict | None:
    """Slack team_id からテナントを取得する。見つからなければ None。"""
    tenant = get_tenant_by_slack_team(team_id)
    if not tenant:
        logger.warning(f"未登録テナント: {team_id}")
    return tenant


# ============================================================
# アプリ・サービス初期化
# ============================================================

app = App(token=SLACK_BOT_TOKEN)

sheets: SheetsManager | None = None
if GOOGLE_SHEET_ID:
    sheets = SheetsManager(GOOGLE_SHEET_ID)
    logger.info("Google Sheets 連携: 有効")
else:
    logger.warning("GOOGLE_SHEET_ID 未設定 — Sheets 同期は無効")


# ============================================================
# ユーティリティ
# ============================================================

def _get_employee_name(client, user_id: str) -> str:
    """Slack ユーザーIDから表示名を取得する"""
    try:
        info = client.users_info(user=user_id)
        profile = info["user"]["profile"]
        return profile.get("real_name") or profile.get("display_name") or user_id
    except Exception:
        return user_id


def _fmt_yen(amount: int) -> str:
    return f"¥{amount:,}"


def _download_file(url: str, token: str) -> bytes:
    resp = http_requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


# ============================================================
# file_shared イベント
# ============================================================

@app.event("file_public")
def handle_file_public(event, logger):
    """file_public は channel_id を持たないため無視する（file_shared で処理済み）"""
    logger.debug(f"file_public 無視: file={event.get('file_id')}")

@app.event("file_shared")
def handle_file_shared(event, client, logger):
    channel_id = event.get("channel_id")
    file_id    = event.get("file_id")
    user_id    = event.get("user_id", "")

    logger.info(f"file_shared: file={file_id} channel={channel_id} user={user_id}")

    # テナント取得
    team_info = client.team_info()
    slack_team_id = team_info["team"]["id"]
    tenant = get_tenant_by_slack_team(slack_team_id)
    if not tenant:
        logger.error(f"テナント未登録: {slack_team_id}")
        return
    tenant_id = tenant["id"]

    # DM以外からの投稿を無視
    if channel_id and not channel_id.startswith("D"):
        logger.info(f"チャンネル投稿を無視: {channel_id}")
        return

    # 処理中メッセージ（申請者のDMに返信）
    post = client.chat_postMessage(channel=channel_id, text="📷 領収書を解析中…")
    msg_ts = post["ts"]
    # 承認カードの送信先（財務承認チャンネル）
    approval_channel = APPROVAL_CHANNEL_ID or channel_id

    try:
        # ファイル情報取得
        file_info = client.files_info(file=file_id)["file"]
        mime = file_info.get("mimetype", "")

        # PDF も受け付ける
        if not (mime.startswith("image/") or mime == "application/pdf"):
            client.chat_update(
                channel=channel_id, ts=msg_ts,
                text="⚠️ 画像または PDF ファイルをアップロードしてください。",
            )
            return

        # 社員名取得
        employee_name = _get_employee_name(client, user_id) if user_id else "不明"

        # ファイルダウンロード
        file_bytes = _download_file(file_info["url_private"], SLACK_BOT_TOKEN)
        temp_path  = f"/tmp/receipt_{file_id}.jpg"
        with open(temp_path, "wb") as f:
            f.write(file_bytes)
        logger.info(f"ダウンロード完了: {len(file_bytes):,} bytes")

        # OCR
        ocr_result = parse_receipt(temp_path)
        os.remove(temp_path)

        # 仕訳生成
        event_date = ocr_result.event_date or datetime.now().strftime("%Y-%m-%d")
        seq        = get_next_sequence(event_date, tenant_id)
        event_id   = generate_event_id(event_date, seq)

        # Claude AI で全項目を一括判定
        from core.ai_classifier import classify
        ai_result = classify(ocr_result.raw_text, ocr_result.counterparty)

        # Claude結果でOCR結果を上書き
        if ai_result:
            if ai_result.get("counterparty"):
                ocr_result.counterparty = ai_result["counterparty"]
            if ai_result.get("event_date"):
                ocr_result.event_date = ai_result["event_date"]
            # AI が event_date を上書きした場合、event_id を再生成
            event_date = ocr_result.event_date or datetime.now().strftime("%Y-%m-%d")
            seq        = get_next_sequence(event_date, tenant_id)
            event_id   = generate_event_id(event_date, seq)
            if ai_result.get("total_amount"):
                ocr_result.total_amount = int(ai_result["total_amount"])
            if ai_result.get("taxable_10_amount"):
                ocr_result.taxable_10_amount = int(ai_result["taxable_10_amount"])
            if ai_result.get("tax_10_amount"):
                ocr_result.tax_10_amount = int(ai_result["tax_10_amount"])
            if ai_result.get("taxable_8_amount") is not None:
                ocr_result.taxable_8_amount = int(ai_result["taxable_8_amount"])
            if ai_result.get("tax_8_amount") is not None:
                ocr_result.tax_8_amount = int(ai_result["tax_8_amount"])
            if ai_result.get("invoice_number"):
                ocr_result.invoice_number = ai_result["invoice_number"]
                ocr_result.has_invoice = True


        entry = build_journal_entry(
            ocr_result       = ocr_result,
            employee_name    = employee_name,
            employee_slack_id= user_id,
            event_id         = event_id,
            raw_text         = ocr_result.raw_text,
        )

        # Claude判定の科目で上書き
        if ai_result.get("debit_account"):
            entry.debit_account = ai_result["debit_account"]
        if ai_result.get("debit_subsidiary"):
            ocr_result.debit_subsidiary = ai_result["debit_subsidiary"]
        from core.accounting import build_credit_account
        entry.credit_account = build_credit_account(employee_name)

        # DB保存
        db_dict = entry.to_db_dict()
        db_dict["employee_slack_id"] = user_id
        db_dict["evidence_url"] = file_info.get("url_private", "")
        db_dict["source_type"]  = "expense"
        insert_event(db_dict, tenant_id)

        # Google Drive に証憑を保存（電子帳簿保存法対応）
        try:
            from core.drive_storage import upload_receipt
            drive_entry = {
                "event_id":     entry.event_id,
                "event_date":   entry.event_date,
                "total_amount": entry.total_amount,
                "counterparty": entry.counterparty,
            }
            drive_url = upload_receipt(
                image_bytes      = file_bytes,
                original_filename= file_info.get("name", "receipt.jpg"),
                entry            = drive_entry,
                mime_type        = file_info.get("mimetype", "image/jpeg"),
            )
            if drive_url:
                # Drive URLをDBに更新
                from core.database import _get_conn
                with _get_conn(tenant_id) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE accounting_events SET evidence_url=%s WHERE event_id=%s AND tenant_id=%s",
                            (drive_url, entry.event_id, tenant_id)
                        )
                logger.info(f"Drive URL 保存完了: {drive_url}")
        except Exception as drive_err:
            logger.warning(f"Drive アップロードスキップ: {drive_err}")

        # タイムスタンプ付与（RFC3161）
        try:
            from core.timestamp import apply_timestamp, save_timestamp_to_db
            ts_result = apply_timestamp(file_bytes)
            if ts_result:
                save_timestamp_to_db(entry.event_id, tenant_id, ts_result)
                logger.info(f"タイムスタンプ付与完了: {entry.event_id}")
        except Exception as ts_err:
            logger.warning(f"タイムスタンプ付与スキップ: {ts_err}")

        # 申請者DMに登録済通知
        client.chat_update(
            channel=channel_id, ts=msg_ts,
            text=(
                "📋 *登録済*\n\n管理ID: `" + str(entry.event_id) + "`\n取引先: " + str(entry.counterparty) + "\n金額: " + _fmt_yen(entry.total_amount) + "\n日付: " + str(entry.event_date) + "\n科目: " + str(entry.debit_account) + "\n\n財務担当者の承認をお待ちください。"
            ),
        )

        # 承認カードを財務承認チャンネルに送信（申請者のSlack IDをvalueに含める）
        approval_msg = _send_approval_card(
            client, approval_channel, None,
            entry, ocr_result.used_real_ocr,
            applicant_slack_id=user_id,
        )
        # 承認カードに領収書画像を添付
        try:
            client.files_upload_v2(
                channel=approval_channel,
                file=file_bytes,
                filename=file_info.get("name", "receipt.jpg"),
                title=f"領収書: {entry.counterparty} {_fmt_yen(entry.total_amount)}",
            )
        except Exception as img_err:
            logger.warning(f"画像添付スキップ: {img_err}")
        logger.info(f"処理完了: {event_id}")

    except Exception as e:
        logger.error(f"エラー: {e}", exc_info=True)
        try:
            client.chat_update(
                channel=channel_id, ts=msg_ts,
                text=f"❌ エラーが発生しました: {e}",
            )
        except Exception:
            pass
        return


# ============================================================
# 承認カード
# ============================================================

def _send_approval_card(client, channel_id, msg_ts, entry, used_real_ocr: bool, applicant_slack_id: str = ""):
    # msg_tsがNoneの場合は新規投稿、あれば既存メッセージを更新
    from core.accounting import JournalEntry
    e: JournalEntry = entry

    ocr_badge = "🤖 Google Vision" if used_real_ocr else "🎭 シミュレーション"
    from core.timestamp import get_timestamp_badge
    evt_dict = e.to_db_dict()
    ts_badge = get_timestamp_badge(evt_dict)
    from core.accounting import get_invoice_deduction_rate
    from datetime import date
    if e.invoice_number:
        inv_badge = f"✅ T番号照合済 → 消費税控除対象\n{e.invoice_number}"
    else:
        rate, label = get_invoice_deduction_rate(e.event_date)
        pct = int(rate * 100)
        inv_badge = f"⚠️ T番号なし → 経費計上可・消費税控除不可\n現在の控除率: {pct}%（{label}）"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🧾 経費申請"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*管理ID*\n`{e.event_id}`"},
                {"type": "mrkdwn", "text": f"*OCRモード*\n{ocr_badge}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*申請者*\n{e.employee_name}"},
                {"type": "mrkdwn", "text": f"*発生日*\n{e.event_date}"},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*取引先*\n{e.counterparty}"},
                {"type": "mrkdwn", "text": f"*税込金額*\n{_fmt_yen(e.total_amount)}"},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*借方科目*\n{e.debit_account}"},
                {"type": "mrkdwn", "text": f"*貸方科目*\n{e.credit_account}"},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*税率10%対象額*\n{_fmt_yen(e.taxable_10_amount)}"},
                {"type": "mrkdwn", "text": f"*消費税(10%)*\n{_fmt_yen(e.tax_10_amount)}"},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*税率8%対象額*\n{_fmt_yen(e.taxable_8_amount)}"},
                {"type": "mrkdwn", "text": f"*消費税(8%)*\n{_fmt_yen(e.tax_8_amount)}"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*T番号*: {inv_badge}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*電帳法*: {ts_badge}"}},
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"actions_{e.event_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ 承認"},
                    "style": "primary",
                    "action_id": "approve_expense",
                    "value": f"{e.event_id}|{applicant_slack_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ 却下"},
                    "style": "danger",
                    "action_id": "reject_expense",
                    "value": f"{e.event_id}|{applicant_slack_id}",
                },
            ],
        },
    ]

    if msg_ts:
        client.chat_update(
            channel=channel_id, ts=msg_ts,
            text="🧾 経費申請", blocks=blocks,
        )
    else:
        client.chat_postMessage(
            channel=channel_id,
            text="🧾 経費申請", blocks=blocks,
        )


def _send_duplicate_warning(client, channel_id, msg_ts, dup: dict, ocr_result):
    text = (
        f"⚠️ *重複した領収書を検出しました*\n\n"
        f"• 既存管理ID: `{dup['event_id']}`\n"
        f"• 取引先: {dup['counterparty']}\n"
        f"• 金額: {_fmt_yen(ocr_result.total_amount)}\n"
        f"• T番号: {ocr_result.invoice_number}\n"
        f"• ステータス: {dup['status']}\n\n"
        "この領収書は既に登録済みです。処理をスキップしました。"
    )
    client.chat_update(
        channel=channel_id, ts=msg_ts,
        text="⚠️ 重複検出",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    )


# ============================================================
# 承認ボタン
# ============================================================

@app.action("approve_expense")
def handle_approve(ack, body, client, logger):
    ack()
    raw_value  = body["actions"][0]["value"]
    event_id, applicant_slack_id = (raw_value.split("|", 1) + [""])[:2] if "|" in raw_value else (raw_value, "")
    approver   = body["user"]["id"]
    channel_id = body["channel"]["id"]
    msg_ts     = body["message"]["ts"]

    logger.info(f"承認: {event_id} by {approver}")

    try:
        # DB更新
        tenant = _get_tenant(body.get("team", {}).get("id", ""))
        tenant_id = tenant["id"] if tenant else None
        update_status(event_id, "業務承認済", tenant_id, approved_by=approver)

        # Google Sheets 同期
        if sheets:
            evt = get_event_by_id(event_id, tenant_id)
            if evt:
                from core.accounting import JournalEntry
                entry = JournalEntry(
                    event_id          = evt["event_id"],
                    event_date        = str(evt["event_date"]),
                    counterparty      = evt["counterparty"],
                    total_amount      = evt["amount"],
                    taxable_10_amount = evt.get("taxable_10_amount", 0),
                    tax_10_amount     = evt.get("tax_10_amount", 0),
                    taxable_8_amount  = evt.get("taxable_8_amount", 0),
                    tax_8_amount      = evt.get("tax_8_amount", 0),
                    debit_account     = evt["debit_account"],
                    credit_account    = evt["credit_account"],
                    invoice_number    = evt.get("invoice_number"),
                    has_invoice       = bool(evt.get("has_invoice")),
                    employee_name     = evt.get("employee_name", ""),
                    status            = "業務承認済",
                    evidence_url      = evt.get("evidence_url", ""),
                )
                ok = sheets.write_journal_entry(entry)
                if ok:
                    logger.info(f"Sheets 同期完了: {event_id}")
                else:
                    logger.warning(f"Sheets 同期失敗: {event_id}")

        # Phase 2: 会計ソフトへ自動計上
        accounting_msg = ""
        try:
            from adapters import post_to_accounting_software
            if evt:
                from core.accounting import JournalEntry
                acc_result = post_to_accounting_software(entry)
                if acc_result["software"] != "none":
                    icon = "✅" if acc_result["success"] else "⚠️"
                    accounting_msg = f"\n{icon} {acc_result['message']}"
        except Exception as acc_e:
            logger.warning(f"会計ソフト連携スキップ: {acc_e}")

        approver_name = _get_employee_name(client, approver)

        # #経費承認チャンネルの承認カードを更新
        client.chat_update(
            channel=channel_id, ts=msg_ts,
            text=f"✅ 承認済: {event_id}",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"✅ *承認されました*\n\n"
                        f"管理ID: `{event_id}`\n"
                        f"承認者: {approver_name}\n"
                        f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        f"{accounting_msg}"
                    ),
                },
            }],
        )

        # 申請者DMに承認済通知
        if applicant_slack_id:
            try:
                client.chat_postMessage(
                    channel=applicant_slack_id,
                    text=(
                        f"✅ *承認済*\n\n"
                        f"管理ID: `{event_id}`\n"
                        f"承認者: {approver_name}\n"
                        f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                        f"経費が承認されました。"
                    ),
                )
            except Exception as dm_err:
                logger.warning(f"申請者DM送信失敗: {dm_err}")

    except Exception as e:
        logger.error(f"承認エラー: {e}", exc_info=True)


# ============================================================
# 却下ボタン
# ============================================================

@app.action("reject_expense")
def handle_reject(ack, body, client, logger):
    ack()
    raw_value  = body["actions"][0]["value"]
    event_id, applicant_slack_id = (raw_value.split("|", 1) + [""])[:2] if "|" in raw_value else (raw_value, "")
    rejector   = body["user"]["id"]
    channel_id = body["channel"]["id"]
    msg_ts     = body["message"]["ts"]

    logger.info(f"却下: {event_id} by {rejector} applicant={applicant_slack_id} raw={raw_value}")

    try:
        tenant = _get_tenant(body.get("team", {}).get("id", ""))
        tenant_id = tenant["id"] if tenant else None
        update_status(event_id, "却下", tenant_id, approved_by=rejector)
        rejector_name = _get_employee_name(client, rejector)

        # #経費承認チャンネルの承認カードを更新
        client.chat_update(
            channel=channel_id, ts=msg_ts,
            text=f"❌ 却下: {event_id}",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"❌ *却下されました*\n\n"
                        f"管理ID: `{event_id}`\n"
                        f"却下者: {rejector_name}\n"
                        f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    ),
                },
            }],
        )

        # 申請者DMに却下通知
        if applicant_slack_id:
            try:
                client.chat_postMessage(
                    channel=applicant_slack_id,
                    text=(
                        f"❌ *却下されました*\n\n"
                        f"管理ID: `{event_id}`\n"
                        f"却下者: {rejector_name}\n"
                        f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                        f"内容を確認の上、再申請してください。"
                    ),
                )
            except Exception as dm_err:
                logger.warning(f"申請者DM送信失敗: {dm_err}")

    except Exception as e:
        logger.error(f"却下エラー: {e}", exc_info=True)


# ============================================================
# @メンション（ヘルプ）
# ============================================================

@app.command("/export")
def handle_export(ack, body, client, logger):
    """
    /export [YYYY-MM] [format] コマンド:
    format: yayoi(default) / freee / mf
    例: /export 2026-04 freee
    """
    ack()
    user_id    = body["user_id"]
    channel_id = body["channel_id"]
    text       = body.get("text", "").strip()
    parts      = text.split()
    fmt        = "yayoi"
    FORMATS    = ("freee", "mf", "yayoi", "csv")
    if len(parts) >= 2 and parts[-1].lower() in FORMATS:
        fmt  = parts[-1].lower()
        text = parts[0] if len(parts) >= 2 else ""
    elif len(parts) == 1 and parts[0].lower() in FORMATS:
        fmt  = parts[0].lower()
        text = ""
    else:
        text = parts[0] if parts else ""

    # 対象月を決定
    from datetime import datetime
    if text:
        try:
            target = datetime.strptime(text, "%Y-%m")
        except ValueError:
            client.chat_postMessage(
                channel=channel_id,
                text="❌ 形式が正しくありません。例: `/export 2026-03`"
            )
            return
    else:
        target = datetime.now()

    year, month = target.year, target.month
    ym_label = f"{year:04d}/{month:02d}"

    # DB から承認済みイベントを取得
    from core.database import list_all_events_by_month
    tenant = _get_tenant(body.get("team_id", ""))
    tenant_id = tenant["id"] if tenant else None
    events = [e for e in list_all_events_by_month(year, month, tenant_id)
              if e.get("status") == "業務承認済"]

    if not events:
        client.chat_postMessage(
            channel=channel_id,
            text=f"📭 {ym_label} の承認済み仕訳が見つかりません。"
        )
        return

    # CSV生成（形式選択）
    if fmt == "freee":
        from core.csv_export import build_freee_csv
        csv_bytes = build_freee_csv(events)
        filename  = f"freee_{year:04d}{month:02d}.csv"
        fmt_label = "freee"
        fmt_note  = "freee会計 → 会計帳簿 → 仕訳帳 → インポートで取り込んでください。"
    elif fmt == "mf":
        from core.csv_export import build_mf_csv
        csv_bytes = build_mf_csv(events)
        filename  = f"mf_{year:04d}{month:02d}.csv"
        fmt_label = "マネーフォワード"
        fmt_note  = "MFクラウド会計 → 仕訳帳 → インポートで取り込んでください。"
    elif fmt == "csv":
        from core.csv_export import build_generic_csv
        csv_bytes = build_generic_csv(events)
        filename  = f"journal_{year:04d}{month:02d}.csv"
        fmt_label = "汎用"
        fmt_note  = "勘定奉行・PCA・TKC・MJS・JDL等、どの会計ソフトでも読み込み可能な標準形式です。"
    else:
        from core.yayoi_export import build_yayoi_csv
        csv_bytes = build_yayoi_csv(events)
        filename  = f"yayoi_{year:04d}{month:02d}.csv"
        fmt_label = "弥生"
        fmt_note  = "弥生会計 → データ読み込み → このファイルを選択してインポートしてください。"

    client.files_upload_v2(
        channel=channel_id,
        content=csv_bytes,
        filename=filename,
        title=f"{fmt_label}インポート用仕訳CSV {ym_label}（{len(events)}件）",
        initial_comment=(
            f"📊 *{ym_label} 承認済み仕訳 — {fmt_label}形式*\n"
            f"件数: {len(events)} 件\n"
            f"{fmt_note}\n"
            f"使い方: `/export YYYY-MM yayoi` / `freee` / `mf` / `csv`"
        ),
    )
    logger.info(f"弥生CSV出力: {filename} ({len(events)}件)")


@app.event("app_mention")
def handle_mention(event, say):
    sheets_status = "有効 ✅" if sheets else "無効 ⚠️ (GOOGLE_SHEET_ID 未設定)"
    say(
        f"こんにちは！*NextAccount v2 Bot* です。\n\n"
        f"*現在の状態*\n"
        f"• OCR: Google Cloud Vision 🤖\n"
        f"• Google Sheets 同期: {sheets_status}\n"
        f"• 重複チェック: 有効 ✅\n\n"
        f"*使い方*\n"
        f"1. このチャンネルに領収書の画像または PDF をアップロード\n"
        f"2. 自動でOCR解析 → 仕訳カード表示\n"
        f"3. ✅ 承認 をクリック\n"
        f"4. 個人の月次 Google シート + 財務部門集計シートに自動記録\n\n"
        f"_仕訳: 借方＝経費科目 / 貸方＝未払費用（社員名）_"
    )


# ============================================================
# エントリポイント
# ============================================================

def start():
    init_database()
    logger.info("=" * 60)
    logger.info("NextAccount v2 Bot 起動")
    logger.info(f"Sheets 連携: {'有効' if sheets else '無効'}")
    logger.info("=" * 60)
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()

# @app.event("message")
# def handle_message(event, logger):
#     """message イベントを明示的に無視（app_mention と競合しないように）"""
#     pass
# 

# ============================================================
# 管理者向け削除コマンド
# ============================================================

@app.command("/delete")
def handle_delete(ack, body, client, logger):
    """
    /delete [event_id] コマンド:
    指定した管理IDのレコードをDBから削除する（管理者専用）
    """
    ack()
    user_id    = body["user_id"]
    channel_id = body["channel_id"]
    event_id   = body.get("text", "").strip()

    if not event_id:
        client.chat_postMessage(
            channel=channel_id,
            text="❌ 管理IDを指定してください。例: `/delete T20260406-00014`"
        )
        return

    # テナント解決
    tenant = _get_tenant(body.get("team_id", ""))
    tenant_id = tenant["id"] if tenant else None

    # レコード存在確認
    evt = get_event_by_id(event_id, tenant_id)
    if not evt:
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ 管理ID `{event_id}` が見つかりません。"
        )
        return

    # DB削除
    try:
        from core.database import _get_conn
        with _get_conn(tenant_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM accounting_events WHERE event_id = %s AND tenant_id = %s",
                    (event_id, tenant_id)
                )
        logger.info(f"削除: {event_id} by {user_id}")
        client.chat_postMessage(
            channel=channel_id,
            text=(
                f"🗑️ *削除完了*\n"
                f"管理ID: `{event_id}`\n"
                f"取引先: {evt.get('counterparty', '')}\n"
                f"金額: ¥{evt.get('amount', 0):,}\n"
                f"削除者: <@{user_id}>"
            )
        )
    except Exception as e:
        logger.error(f"削除エラー: {e}", exc_info=True)
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ 削除に失敗しました: {e}"
        )


# ============================================================
# /setup コマンド（管理者専用・新規テナント初期設定）
# ============================================================

@app.command("/setup")
def handle_setup(ack, body, client, logger):
    """
    /setup [会社名] [メールアドレス]
    新規テナントの Google Sheets を自動作成してDBに保存する。
    """
    ack()
    user_id    = body["user_id"]
    channel_id = body["channel_id"]
    team_id    = body.get("team_id", "")
    text       = body.get("text", "").strip().split()

    if len(text) < 1:
        client.chat_postMessage(
            channel=channel_id,
            text="❌ 使い方: `/setup 会社名 メールアドレス(任意)`\n例: `/setup 株式会社サンプル admin@sample.co.jp`"
        )
        return

    company_name = text[0]
    share_email  = text[1] if len(text) >= 2 else None

    client.chat_postMessage(channel=channel_id, text=f"⚙️ `{company_name}` の Sheets を作成中...")

    try:
        from core.sheets_provisioner import provision_tenant_spreadsheet
        from core.database import get_tenant_by_slack_team, update_tenant_sheet

        sheet_id = provision_tenant_spreadsheet(company_name, share_email)

        tenant = get_tenant_by_slack_team(team_id)
        if tenant:
            update_tenant_sheet(tenant["id"], sheet_id)

        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        client.chat_postMessage(
            channel=channel_id,
            text=(
                f"✅ *セットアップ完了*\n\n"
                f"会社名: {company_name}\n"
                f"Sheet ID: `{sheet_id}`\n"
                f"URL: {sheet_url}\n"
                + (f"共有先: {share_email}" if share_email else "")
            )
        )
        logger.info(f"/setup 完了: {company_name} → {sheet_id}")

    except Exception as e:
        logger.error(f"/setup エラー: {e}", exc_info=True)
        client.chat_postMessage(channel=channel_id, text=f"❌ セットアップ失敗: {e}")




# ============================================================
# 交通費機能（定期券区間管理・申請）
# ============================================================

@app.command("/定期登録")
def handle_commute_register(ack, body, client, logger):
    """
    /commute-register from to
    定期券区間を登録
    例: /commute-register 新宿 渋谷
    """
    ack()
    user_id    = body["user_id"]
    channel_id = body["channel_id"]
    text       = body.get("text", "").strip().split()

    if len(text) < 2:
        client.chat_postMessage(
            channel=channel_id,
            text="❌ 使い方: `/定期登録 新宿 渋谷`"
        )
        return

    commute_from = text[0]
    commute_to   = text[1]

    try:
        tenant = _get_tenant(body.get("team_id", ""))
        tenant_id = tenant["id"] if tenant else None
        employee_name = _get_employee_name(client, user_id)

        from core.database import upsert_user, update_commute_section
        upsert_user(user_id, employee_name, tenant_id)
        update_commute_section(user_id, tenant_id, commute_from, commute_to)

        client.chat_postMessage(
            channel=channel_id,
            text=(
                f"✅ *定期券区間を登録しました*\n\n"
                f"区間: {commute_from} → {commute_to}\n\n"
                f"今後、この区間内での交通費申請は自動で ¥0 として計上されます。"
            )
        )
        logger.info(f"定期券登録: {user_id} ({commute_from}→{commute_to})")

    except Exception as e:
        logger.error(f"定期券登録エラー: {e}", exc_info=True)
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ 登録に失敗しました: {e}"
        )


def _parse_transportation_expense(text: str) -> dict | None:
    """
    「交通費 新宿 渋谷 220」形式をパース（全角・半角スペース対応）
    戻り値: {"from": "新宿", "to": "渋谷", "amount": 220} または None
    """
    import re
    text = text.strip()
    # 全角スペースと半角スペースの両方に対応
    parts = re.split(r'[\s　]+', text)
    if len(parts) < 4 or parts[0] != "交通費":
        return None
    
    try:
        return {
            "from": parts[1],
            "to": parts[2],
            "amount": int(parts[3])
        }
    except (IndexError, ValueError):
        return None


@app.event("message")
def handle_transportation_message(event, client, logger):
    """
    メッセージで「交通費」コマンドを認識
    「交通費 新宿 渋谷 220」形式で交通費を申請
    """
    # チャンネル ID を取得
    channel_id = event.get("channel", "")

    user_id = event.get("user", "")
    text = event.get("text", "").strip()
    
    # 「交通費」コマンドでなければスキップ
    trans = _parse_transportation_expense(text)
    if not trans:
        return

    logger.info(f"交通費申請: user={user_id} from={trans['from']} to={trans['to']} amount={trans['amount']}")

    try:
        team_info = client.team_info()
        slack_team_id = team_info["team"]["id"]
        tenant = get_tenant_by_slack_team(slack_team_id)
        if not tenant:
            logger.error(f"テナント未登録: {slack_team_id}")
            return
        tenant_id = tenant["id"]

        # ユーザー情報取得
        from core.database import get_user_by_slack_id, upsert_user
        user = get_user_by_slack_id(user_id, tenant_id)
        employee_name = _get_employee_name(client, user_id) if user_id else "不明"
        
        # 初回ならユーザー登録
        if not user:
            upsert_user(user_id, employee_name, tenant_id)
            user = get_user_by_slack_id(user_id, tenant_id)

        # 定期券区間チェック
        actual_amount = trans["amount"]
        is_commute_section = False
        if user and user.get("commute_from") and user.get("commute_to"):
            if user["commute_from"] == trans["from"] and user["commute_to"] == trans["to"]:
                actual_amount = 0
                is_commute_section = True

        # 仕訳生成
        seq = get_next_sequence(event_date, tenant_id)
        event_id = generate_event_id(event_date, seq)

        # 交通費仕訳
        entry = {
            "event_id": event_id,
            "event_date": event_date,
            "counterparty": f"{trans['from']}→{trans['to']}",
            "amount": actual_amount,
            "taxable_10_amount": 0,
            "tax_10_amount": 0,
            "taxable_8_amount": 0,
            "tax_8_amount": 0,
            "debit_account": "旅費交通費",
            "credit_account": "未払費用",
            "invoice_number": None,
            "has_invoice": False,
            "employee_name": employee_name,
            "employee_slack_id": user_id,
            "status": "申請中",
            "source_type": "transportation",
        }

        # DB保存
        insert_event(entry, tenant_id)

        # 確認メッセージ
        section_note = "（定期区間内のため ¥0 で登録）" if is_commute_section else ""
        post = client.chat_postMessage(
            channel=channel_id,
            text=(
                f"🚆 *交通費申請*\n\n"
                f"経路: {trans['from']} → {trans['to']}\n"
                f"金額: {_fmt_yen(actual_amount)} {section_note}\n"
                f"管理ID: `{event_id}`\n\n"
                f"財務担当者の承認をお待ちください。"
            )
        )
        msg_ts = post["ts"]

        # 承認カードを財務承認チャンネルに送信
        _send_approval_card(
            client, APPROVAL_CHANNEL_ID or channel_id, None,
            _create_transportation_entry(entry),
            used_real_ocr=False,
            applicant_slack_id=user_id,
        )

        logger.info(f"交通費登録完了: {event_id}")

    except Exception as e:
        logger.error(f"交通費申請エラー: {e}", exc_info=True)
        try:
            client.chat_postMessage(
                channel=channel_id,
                text=f"❌ エラーが発生しました: {e}"
            )
        except Exception:
            pass


def _create_transportation_entry(entry_dict):
    """辞書から JournalEntry を作成（交通費用）"""
    from core.accounting import JournalEntry
    return JournalEntry(
        event_id=entry_dict["event_id"],
        event_date=entry_dict["event_date"],
        counterparty=entry_dict["counterparty"],
        total_amount=entry_dict["amount"],
        taxable_10_amount=entry_dict.get("taxable_10_amount", 0),
        tax_10_amount=entry_dict.get("tax_10_amount", 0),
        taxable_8_amount=entry_dict.get("taxable_8_amount", 0),
        tax_8_amount=entry_dict.get("tax_8_amount", 0),
        debit_account=entry_dict["debit_account"],
        credit_account=entry_dict["credit_account"],
        invoice_number=None,
        has_invoice=False,
        employee_name=entry_dict["employee_name"],
        status="申請中",
    )
