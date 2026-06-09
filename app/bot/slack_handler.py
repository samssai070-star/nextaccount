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
import re
import logging
import requests as http_requests
from datetime import datetime

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from core.config import SLACK_BOT_TOKEN, SLACK_APP_TOKEN, GOOGLE_SHEET_ID
import os
APPROVAL_CHANNEL_ID = os.environ.get("APPROVAL_CHANNEL_ID", "")
from core.ocr import parse_receipt, OcrResult
from core.accounting import build_journal_entry, generate_event_id, build_credit_account
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
    update_event,
)

logger = logging.getLogger(__name__)

# ============================================================
# ファイルID重複処理防止（Slackイベント再送対策）
# ============================================================
import time as _time
_processed_file_ids: dict[str, float] = {}
_FILE_ID_TTL = 180  # 3分間は同一file_idを無視


def _is_duplicate_file_event(file_id: str) -> bool:
    """同一file_idのイベントが3分以内に処理済みならTrueを返す（Slackリプレイ対策）"""
    now = _time.time()
    for fid in list(_processed_file_ids.keys()):
        if now - _processed_file_ids[fid] > _FILE_ID_TTL:
            del _processed_file_ids[fid]
    if file_id in _processed_file_ids:
        return True
    _processed_file_ids[file_id] = now
    return False


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


# 補助科目名 → 主科目名（Claude が補助科目を debit_account に入れた場合の補正用）
_SUBSIDIARY_TO_MAIN = {
    "電車賃": "旅費交通費", "タクシー代": "旅費交通費", "バス代": "旅費交通費",
    "駐車場代": "旅費交通費", "宿泊費": "旅費交通費", "航空券": "旅費交通費",
    "電話代": "通信費", "郵便・宅配": "通信費", "インターネット": "通信費",
    "電気代": "水道光熱費", "ガス代": "水道光熱費", "水道代": "水道光熱費",
    "接待飲食費": "接待交際費", "贈答品費": "接待交際費", "慶弔費": "接待交際費",
    "会議飲食費": "会議費", "会議室費": "会議費",
    "文具・事務用品": "消耗品費", "日用品": "消耗品費",
    "PC周辺機器": "消耗品費", "その他消耗品": "消耗品費",
    "広告費": "広告宣伝費", "印刷費": "広告宣伝費", "デザイン費": "広告宣伝費",
    "事務所家賃": "地代家賃", "駐車場月極": "地代家賃",
    "業務委託費": "外注費", "外注費": "外注費",
    "設備修繕費": "修繕費", "機器修理費": "修繕費",
}

_VALID_MAIN_ACCOUNTS = {
    "旅費交通費", "通信費", "水道光熱費", "接待交際費", "会議費",
    "消耗品費", "広告宣伝費", "地代家賃", "租税公課", "社会保険料",
    "外注費", "福利厚生費", "修繕費", "諸雑費",
}

_SUBSIDIARY_DEFAULT = {
    "旅費交通費": "電車賃",
    "通信費": "電話代",
    "水道光熱費": "電気代",
    "接待交際費": "接待飲食費",
    "会議費": "会議飲食費",
    "消耗品費": "その他消耗品",
    "広告宣伝費": "広告費",
    "地代家賃": "事務所家賃",
    "租税公課": "租税公課",
    "社会保険料": "社会保険料",
    "外注費": "業務委託費",
    "修繕費": "設備修繕費",
    "諸雑費": "諸雑費",
}

# 取引先名から補助科目を推定するキーワードマッピング
_COUNTERPARTY_SUBSIDIARY = [
    (re.compile(r"タイムズ|パーキング|駐車|コインパーク|NPC|三井リパーク|ザ・パーク|リパーク", re.I), "駐車場代"),
    (re.compile(r"タクシー|ハイヤー|DiDi|Uber|GO|エスライド", re.I), "タクシー代"),
    (re.compile(r"航空|ANA|JAL|スカイマーク|ジェットスター|ピーチ", re.I), "航空券"),
    (re.compile(r"ホテル|旅館|宿|イン$|inn", re.I), "宿泊費"),
    (re.compile(r"郵便|ヤマト|佐川|日通|クロネコ|ゆうパック", re.I), "郵便・宅配"),
    (re.compile(r"NTT|ドコモ|au|ソフトバンク|楽天モバイル|KDDI", re.I), "電話代"),
    (re.compile(r"電力|東電|関電|中電|九電|東北電|北電|四電|沖電", re.I), "電気代"),
    (re.compile(r"ガス|東京ガス|大阪ガス|東邦ガス", re.I), "ガス代"),
]


def _default_subsidiary(debit_account: str, counterparty: str = "") -> str:
    """勘定科目・取引先名から補助科目を推定する"""
    if counterparty and "旅費交通費" in debit_account:
        for pattern, subsidiary in _COUNTERPARTY_SUBSIDIARY:
            if pattern.search(counterparty):
                return subsidiary
    for key, val in _SUBSIDIARY_DEFAULT.items():
        if key in debit_account:
            return val
    return debit_account


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

    # 同一ファイルの重複イベントをスキップ（Slackリプレイ・二重送信対策）
    if _is_duplicate_file_event(file_id):
        logger.warning(f"重複ファイルイベントをスキップ: {file_id}")
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
        ext = ".pdf" if mime == "application/pdf" else ".jpg"
        temp_path  = f"/tmp/receipt_{file_id}{ext}"
        with open(temp_path, "wb") as f:
            f.write(file_bytes)
        logger.info(f"ダウンロード完了: {len(file_bytes):,} bytes")

        # Claude Multimodal で画像から全項目を直接抽出
        from core.ai_classifier import extract_all_by_claude_vision, classify

        if mime.startswith("image/"):
            ai_result = extract_all_by_claude_vision(file_bytes, mime)
        else:
            ai_result = {}

        if ai_result:
            # Claude Vision成功 → OcrResultに変換
            logger.info(f"Claude Vision生結果: counterparty={ai_result.get('counterparty')} "
                        f"event_date={ai_result.get('event_date')} "
                        f"total={ai_result.get('total_amount')} "
                        f"tax10={ai_result.get('tax_10_amount')} "
                        f"invoice={ai_result.get('invoice_number')} "
                        f"reason={ai_result.get('reason')}")
            ocr_result = OcrResult(used_real_ocr=True)
            counterparty = ai_result.get("counterparty") or "不明"
            # NTT社名誤読補正（東→海 等の誤認識）
            counterparty = re.sub(r"NTT[^\s]*海日本", "NTT東日本", counterparty)
            counterparty = re.sub(r"NTT[^\s]*果日本", "NTT東日本", counterparty)
            ocr_result.counterparty = counterparty
            raw_date = ai_result.get("event_date") or ""
            # YY-MM-DD → 2025年と誤って和暦変換された場合を補正（例: 2013→2025）
            date_m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(raw_date))
            if date_m:
                y = int(date_m.group(1))
                if y < 2000:  # 和暦換算ミスの可能性（昭和/平成）
                    # 末2桁を20XX年に強制補正
                    corrected_y = 2000 + (y % 100)
                    raw_date = f"{corrected_y}-{date_m.group(2)}-{date_m.group(3)}"
                    logger.warning(f"日付年補正: {y} → {corrected_y} ({raw_date})")
            ocr_result.event_date        = raw_date or None
            total      = int(ai_result.get("total_amount") or 0)
            tax_10     = int(ai_result.get("tax_10_amount") or 0)
            taxable_10 = int(ai_result.get("taxable_10_amount") or 0)
            tax_8      = int(ai_result.get("tax_8_amount") or 0)
            taxable_8  = int(ai_result.get("taxable_8_amount") or 0)
            # 税額から合計を逆算して整合性チェック（郵便払込票等の誤読検出）
            tax_total = taxable_10 + tax_10 + taxable_8 + tax_8
            if tax_10 > 0 and tax_total > 0 and total > 0:
                implied = taxable_10 + tax_10 + taxable_8 + tax_8
                if implied > 0 and abs(total - implied) > implied * 0.05:
                    # 合計と税額合計が5%以上乖離 → 税額ベースで合計を補正
                    logger.warning(f"金額整合性NG: total={total} tax_implied={implied} → 補正")
                    total = implied
            ocr_result.total_amount      = total
            ocr_result.taxable_10_amount = taxable_10
            ocr_result.tax_10_amount     = tax_10
            ocr_result.taxable_8_amount  = taxable_8
            ocr_result.tax_8_amount      = tax_8
            inv = ai_result.get("invoice_number")
            if inv and str(inv).strip().lower() != "null":
                inv_clean = re.sub(r"[-ー－]", "", str(inv).strip())  # ハイフン除去
                if re.match(r"^T\d{13}$", inv_clean):
                    ocr_result.invoice_number = inv_clean
                    ocr_result.has_invoice    = True
                else:
                    logger.warning(f"T番号フォーマット不正（ハイフン除去後）: {inv_clean}")
        else:
            # フォールバック: Google Vision OCR → Claude テキスト分類
            logger.warning("Claude Vision失敗 → Google Vision OCRにフォールバック")
            ocr_result = parse_receipt(temp_path)
            fallback_ai = classify(ocr_result.raw_text, ocr_result.counterparty)
            ai_result = fallback_ai or {}
            if ai_result:
                if ai_result.get("counterparty"):
                    ocr_result.counterparty = ai_result["counterparty"]
                if ai_result.get("event_date"):
                    ocr_result.event_date = ai_result["event_date"]
                ai_total  = int(ai_result["total_amount"]) if ai_result.get("total_amount") else 0
                ocr_total = ocr_result.total_amount
                if ocr_total == 0 and ai_total > 0:
                    ocr_result.total_amount = ai_total
                elif ai_total > 0 and ocr_total > 0 and ocr_total < ai_total / 10:
                    ocr_result.total_amount = ai_total
                ai_tax_10     = int(ai_result["tax_10_amount"])     if ai_result.get("tax_10_amount")     else 0
                ai_taxable_10 = int(ai_result["taxable_10_amount"]) if ai_result.get("taxable_10_amount") else 0
                ai_tax_8      = int(ai_result["tax_8_amount"])      if ai_result.get("tax_8_amount")      else 0
                ai_taxable_8  = int(ai_result["taxable_8_amount"])  if ai_result.get("taxable_8_amount")  else 0
                final_total = ocr_result.total_amount
                if final_total > 0 and (ai_taxable_10 + ai_tax_10 + ai_taxable_8 + ai_tax_8) <= final_total:
                    ocr_result.tax_10_amount     = ai_tax_10
                    ocr_result.taxable_10_amount = ai_taxable_10
                    ocr_result.tax_8_amount      = ai_tax_8
                    ocr_result.taxable_8_amount  = ai_taxable_8
                if ai_result.get("invoice_number"):
                    ocr_result.invoice_number = ai_result["invoice_number"]
                    ocr_result.has_invoice = True

        try:
            os.remove(temp_path)
        except Exception:
            pass

        # 仕訳生成
        event_date  = ocr_result.event_date or datetime.now().strftime("%Y-%m-%d")
        upload_date = datetime.now().strftime("%Y-%m-%d")
        year_month  = datetime.now().strftime("%Y-%m")
        from core.database import get_or_assign_employee_code, get_next_employee_sequence
        emp_code = get_or_assign_employee_code(user_id, tenant_id, year_month)
        seq      = get_next_employee_sequence(upload_date, emp_code, tenant_id)
        event_id = generate_event_id(upload_date, seq, employee_code=emp_code)

        entry = build_journal_entry(
            ocr_result       = ocr_result,
            employee_name    = employee_name,
            employee_slack_id= user_id,
            event_id         = event_id,
            raw_text         = ocr_result.raw_text,
        )

        # Claude判定の科目で上書き（補助科目名が誤って主科目に入っていたら補正）
        ai_debit = ai_result.get("debit_account", "")
        if ai_debit:
            if ai_debit not in _VALID_MAIN_ACCOUNTS and ai_debit in _SUBSIDIARY_TO_MAIN:
                fixed_main = _SUBSIDIARY_TO_MAIN[ai_debit]
                logger.warning(f"debit_account補正: 補助科目「{ai_debit}」→ 主科目「{fixed_main}」")
                ai_result["debit_account"]    = fixed_main
                ai_result["debit_subsidiary"] = ai_debit
            entry.debit_account = ai_result["debit_account"]
        ai_subsidiary = ai_result.get("debit_subsidiary", "")
        if ai_subsidiary:
            entry.debit_subsidiary = ai_subsidiary
        elif entry.debit_account:
            entry.debit_subsidiary = _default_subsidiary(entry.debit_account, entry.counterparty)
        entry.credit_account = build_credit_account(employee_name)

        # DB保存（重複チェック後）
        db_dict = entry.to_db_dict()
        db_dict["employee_slack_id"] = user_id
        db_dict["evidence_url"] = file_info.get("url_private", "")
        db_dict["source_type"]  = "expense"

        # 入湯税が含まれる場合: 主エントリから入湯税を分割し2仕訳を生成
        nyutou_amount = int(ai_result.get("nyutou_tax_amount") or 0)
        nyutou_entry  = None
        if nyutou_amount > 0 and nyutou_amount < entry.total_amount:
            entry.total_amount -= nyutou_amount
            seq2 = seq + 1  # 主エントリ未挿入のためget_next_sequenceは同番号を返すので+1
            from core.accounting import JournalEntry
            nyutou_entry = JournalEntry(
                event_id          = generate_event_id(upload_date, seq2, employee_code=emp_code),
                event_date        = entry.event_date,
                counterparty      = entry.counterparty,
                total_amount      = nyutou_amount,
                taxable_10_amount = 0,
                tax_10_amount     = 0,
                taxable_8_amount  = 0,
                tax_8_amount      = 0,
                invoice_number    = None,
                has_invoice       = False,
                debit_account     = "租税公課",
                debit_subsidiary  = "入湯税",
                credit_account    = build_credit_account(employee_name),
                employee_name     = employee_name,
                status            = "申請中",
                evidence_url      = db_dict.get("evidence_url", ""),
                purpose           = f"入湯税（{entry.event_id}から分割）",
            )
            logger.info(f"入湯税分割: 主={entry.event_id} ¥{entry.total_amount} / 租税公課={nyutou_entry.event_id} ¥{nyutou_amount}")

        dup = check_duplicate(entry.invoice_number, entry.total_amount, entry.event_date, tenant_id)
        if dup:
            _send_duplicate_warning(client, channel_id, msg_ts, dup, ocr_result)
            logger.warning(f"重複検出 → スキップ: {dup['event_id']}")
            return

        insert_event(db_dict, tenant_id)
        if nyutou_entry:
            nyutou_db = nyutou_entry.to_db_dict()
            nyutou_db["employee_slack_id"] = user_id
            nyutou_db["evidence_url"]      = db_dict.get("evidence_url", "")
            nyutou_db["source_type"]       = "expense"
            insert_event(nyutou_db, tenant_id)

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

        # 申請者DMに登録済通知（承認者と同じリッチ表示 + 修正ボタン）
        dm_action_elements = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✏️ 内容を修正"},
                "action_id": "quick_edit_btn",
                "value": f"{entry.event_id}|{tenant_id}",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📝 用途・補助科目を入力"},
                "action_id": "input_purpose_btn",
                "value": f"{entry.event_id}|{tenant_id}",
                "style": "primary",
            },
        ]
        if entry.debit_account in ("接待交際費", "会議費"):
            dm_action_elements += [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🍽️ 会議費"},
                    "action_id": "switch_to_kaigi_btn",
                    "value": f"{entry.event_id}|{tenant_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🤝 接待交際費"},
                    "action_id": "switch_to_settai_btn",
                    "value": f"{entry.event_id}|{tenant_id}",
                },
            ]

        dm_blocks = _build_entry_blocks(entry, ocr_result.used_real_ocr) + [
            {"type": "actions", "elements": dm_action_elements},
        ]
        client.chat_update(
            channel=channel_id, ts=msg_ts,
            text=f"🧾 登録済 {entry.counterparty} {_fmt_yen(entry.total_amount)}",
            blocks=dm_blocks,
        )
        from core.database import save_uploader_dm_info
        save_uploader_dm_info(entry.event_id, tenant_id, channel_id, msg_ts)

        # 飲料代が含まれる場合は振り分け選択を促す
        beverage_amount = int(ai_result.get("beverage_amount") or 0)
        if beverage_amount > 0 and entry.debit_account == "旅費交通費":
            client.chat_postMessage(
                channel=channel_id,
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"🍺 *飲料代 ¥{beverage_amount:,} を検出*\n"
                                f"ビール・日本酒・ウイスキー等が含まれています。\n"
                                f"用途に応じて `/edit {entry.event_id}` で科目を変更してください:\n"
                                f"• 接待目的 → *接待交際費 / 接待飲食費*\n"
                                f"• 社内慰安旅行 → *福利厚生費 / レクリエーション費*"
                            ),
                        },
                    }
                ],
                text=f"🍺 飲料代 ¥{beverage_amount:,} を検出",
            )

        # 入湯税分割仕訳の完了通知
        if nyutou_entry:
            client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"🏨 *入湯税を自動分割しました*\n\n"
                    f"• `{entry.event_id}` 旅費交通費 / 宿泊費: *¥{entry.total_amount:,}* （承認待ち）\n"
                    f"• `{nyutou_entry.event_id}` 租税公課 / 入湯税: *¥{nyutou_amount:,}* （承認待ち・宿泊費承認時に同時登録）"
                ),
            )

        # 承認カードを財務承認チャンネルに送信（申請者のSlack IDをvalueに含める）
        approval_ts = _send_approval_card(
            client, approval_channel, None,
            entry, ocr_result.used_real_ocr,
            applicant_slack_id=user_id,
        )
        if approval_ts:
            from core.database import save_approval_card_info
            save_approval_card_info(entry.event_id, tenant_id, approval_channel, approval_ts)
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

def _build_entry_blocks(entry, used_real_ocr: bool) -> list:
    """承認者・申請者共通の経費情報ブロックを生成する（アクションボタンは含まない）"""
    from core.accounting import JournalEntry, get_invoice_deduction_rate
    from core.timestamp import get_timestamp_badge
    e: JournalEntry = entry

    ocr_badge = "🤖 Claude Vision" if used_real_ocr else "🎭 シミュレーション"
    ts_badge  = get_timestamp_badge(e.to_db_dict())

    if e.invoice_number:
        inv_badge = f"✅ T番号照合済 → 消費税控除対象\n{e.invoice_number}"
    else:
        rate, label = get_invoice_deduction_rate(e.event_date)
        pct = int(rate * 100)
        inv_badge = f"⚠️ T番号なし → 経費計上可・消費税控除不可\n現在の控除率: {pct}%（{label}）"

    return [
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
                {"type": "mrkdwn", "text": f"*借方補助科目*\n{e.debit_subsidiary or '（未設定）'}"},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*貸方科目*\n{e.credit_account}"},
                {"type": "mrkdwn", "text": f"*用途*\n{e.purpose or '—'}"},
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
    ]


def _send_approval_card(client, channel_id, msg_ts, entry, used_real_ocr: bool, applicant_slack_id: str = ""):
    """承認チャンネルに経費申請カード（承認・却下ボタン付き）を送信する"""
    from core.accounting import JournalEntry
    e: JournalEntry = entry

    blocks = _build_entry_blocks(e, used_real_ocr) + [
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
        return msg_ts
    else:
        resp = client.chat_postMessage(
            channel=channel_id,
            text="🧾 経費申請", blocks=blocks,
        )
        return resp.get("ts")


def _refresh_uploader_dm(client, event_id: str, tenant_id: str):
    """編集後にアップロード者DMを最新情報で更新する"""
    from core.database import get_uploader_dm_info
    info = get_uploader_dm_info(event_id, tenant_id)
    if not info:
        return
    channel, ts = info
    evt = get_event_by_id(event_id, tenant_id)
    if not evt:
        return
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
        debit_subsidiary  = evt.get("debit_subsidiary", ""),
        credit_account    = evt["credit_account"],
        invoice_number    = evt.get("invoice_number"),
        has_invoice       = bool(evt.get("has_invoice")),
        employee_name     = evt.get("employee_name", ""),
        status            = evt.get("status", ""),
        evidence_url      = evt.get("evidence_url", ""),
        purpose           = evt.get("purpose", ""),
    )
    dm_action_elements = [
        {"type": "button", "text": {"type": "plain_text", "text": "✏️ 内容を修正"},
         "action_id": "quick_edit_btn", "value": f"{entry.event_id}|{tenant_id}"},
        {"type": "button", "text": {"type": "plain_text", "text": "📝 用途・補助科目を入力"},
         "action_id": "input_purpose_btn", "value": f"{entry.event_id}|{tenant_id}", "style": "primary"},
    ]
    if entry.debit_account in ("接待交際費", "会議費"):
        dm_action_elements += [
            {"type": "button", "text": {"type": "plain_text", "text": "🍽️ 会議費"},
             "action_id": "switch_to_kaigi_btn", "value": f"{entry.event_id}|{tenant_id}"},
            {"type": "button", "text": {"type": "plain_text", "text": "🤝 接待交際費"},
             "action_id": "switch_to_settai_btn", "value": f"{entry.event_id}|{tenant_id}"},
        ]
    blocks = _build_entry_blocks(entry, used_real_ocr=True) + [
        {"type": "actions", "elements": dm_action_elements},
    ]
    try:
        client.chat_update(
            channel=channel, ts=ts,
            text=f"🧾 登録済（更新）{entry.counterparty} {_fmt_yen(entry.total_amount)}",
            blocks=blocks,
        )
        logger.info(f"アップロード者DM更新: {event_id}")
    except Exception as e:
        logger.warning(f"アップロード者DM更新失敗: {e}")


def _refresh_approval_card(client, event_id: str, tenant_id: str):
    """編集後に承認チャンネルのカードを最新情報で更新する"""
    from core.database import get_approval_card_info
    card = get_approval_card_info(event_id, tenant_id)
    if not card:
        return
    channel, ts = card
    evt = get_event_by_id(event_id, tenant_id)
    if not evt or evt.get("status") == "業務承認済":
        return  # 承認済は変更不可なので更新不要
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
        debit_subsidiary  = evt.get("debit_subsidiary", ""),
        credit_account    = evt["credit_account"],
        invoice_number    = evt.get("invoice_number"),
        has_invoice       = bool(evt.get("has_invoice")),
        employee_name     = evt.get("employee_name", ""),
        status            = evt.get("status", ""),
        evidence_url      = evt.get("evidence_url", ""),
        purpose           = evt.get("purpose", ""),
    )
    applicant_slack_id = evt.get("employee_slack_id", "")
    blocks = _build_entry_blocks(entry, used_real_ocr=True) + [
        {
            "type": "actions",
            "block_id": f"actions_{entry.event_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ 承認"},
                    "style": "primary",
                    "action_id": "approve_expense",
                    "value": f"{entry.event_id}|{applicant_slack_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ 却下"},
                    "style": "danger",
                    "action_id": "reject_expense",
                    "value": f"{entry.event_id}|{applicant_slack_id}",
                },
            ],
        },
    ]
    try:
        client.chat_update(
            channel=channel, ts=ts,
            text="🧾 経費申請（更新済）", blocks=blocks,
        )
        logger.info(f"承認カード更新: {event_id}")
    except Exception as e:
        logger.warning(f"承認カード更新失敗: {e}")


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
                    debit_subsidiary  = evt.get("debit_subsidiary", ""),
                    credit_account    = evt["credit_account"],
                    invoice_number    = evt.get("invoice_number"),
                    has_invoice       = bool(evt.get("has_invoice")),
                    employee_name     = evt.get("employee_name", ""),
                    status            = "業務承認済",
                    evidence_url      = evt.get("evidence_url", ""),
                    purpose           = evt.get("purpose", ""),
                )
                ok = sheets.write_journal_entry(entry)
                if ok:
                    logger.info(f"Sheets 同期完了: {event_id}")
                else:
                    logger.warning(f"Sheets 同期失敗: {event_id}")

        # 入湯税リンクエントリも同時承認
        from core.database import get_linked_nyutou_entry
        nyutou_evt = get_linked_nyutou_entry(event_id, tenant_id)
        if nyutou_evt:
            update_status(nyutou_evt["event_id"], "業務承認済", tenant_id, approved_by=approver)
            if sheets:
                from core.accounting import JournalEntry as JE
                nyutou_je = JE(
                    event_id          = nyutou_evt["event_id"],
                    event_date        = str(nyutou_evt["event_date"]),
                    counterparty      = nyutou_evt["counterparty"],
                    total_amount      = nyutou_evt["amount"],
                    taxable_10_amount = nyutou_evt.get("taxable_10_amount", 0),
                    tax_10_amount     = nyutou_evt.get("tax_10_amount", 0),
                    taxable_8_amount  = nyutou_evt.get("taxable_8_amount", 0),
                    tax_8_amount      = nyutou_evt.get("tax_8_amount", 0),
                    debit_account     = nyutou_evt["debit_account"],
                    debit_subsidiary  = nyutou_evt.get("debit_subsidiary", ""),
                    credit_account    = nyutou_evt["credit_account"],
                    invoice_number    = nyutou_evt.get("invoice_number"),
                    has_invoice       = bool(nyutou_evt.get("has_invoice")),
                    employee_name     = nyutou_evt.get("employee_name", ""),
                    status            = "業務承認済",
                    evidence_url      = nyutou_evt.get("evidence_url", ""),
                    purpose           = nyutou_evt.get("purpose", ""),
                )
                sheets.write_journal_entry(nyutou_je)
            logger.info(f"入湯税連動承認: {nyutou_evt['event_id']}")

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
        f"• OCR: Claude Vision (Multimodal) 🤖\n"
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
    event_id   = body.get("text", "").strip().split()[0] if body.get("text", "").strip() else ""

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
# /list コマンド（当月の経費一覧）
# ============================================================

@app.command("/list")
def handle_list(ack, body, client, logger):
    """
    /list [YYYY-MM] [status]
    当月（または指定月）の経費一覧を表示する。
    status: all(default) / pending / approved / rejected
    例: /list 2026-04 approved
    """
    ack()
    channel_id = body["channel_id"]
    text       = body.get("text", "").strip().split()

    STATUS_ALIAS = {
        "pending":  "申請中",
        "approved": "業務承認済",
        "rejected": "却下",
        "all":      None,
    }

    target_month = None
    filter_status = None

    for part in text:
        if re.match(r"^\d{4}-\d{2}$", part):
            target_month = part
        elif part.lower() in STATUS_ALIAS:
            filter_status = STATUS_ALIAS[part.lower()]

    from datetime import datetime as _dt
    if target_month:
        try:
            dt = _dt.strptime(target_month, "%Y-%m")
        except ValueError:
            client.chat_postMessage(channel=channel_id, text="❌ 形式: `/list 2026-04` または `/list 2026-04 approved`")
            return
    else:
        dt = _dt.now()

    year, month = dt.year, dt.month
    ym_label = f"{year:04d}/{month:02d}"

    tenant = _get_tenant(body.get("team_id", ""))
    tenant_id = tenant["id"] if tenant else None

    from core.database import list_all_events_by_month
    events = list_all_events_by_month(year, month, tenant_id)

    if filter_status:
        events = [e for e in events if e.get("status") == filter_status]

    if not events:
        status_label = f"（{filter_status}）" if filter_status else ""
        client.chat_postMessage(
            channel=channel_id,
            text=f"📭 {ym_label} の経費{status_label}が見つかりません。"
        )
        return

    STATUS_ICON = {"申請中": "⏳", "業務承認済": "✅", "却下": "❌"}

    lines = [f"*📋 {ym_label} 経費一覧 — {len(events)} 件*\n"]
    for e in events[:20]:
        icon = STATUS_ICON.get(e.get("status", ""), "•")
        lines.append(
            f"{icon} `{e['event_id']}` {e['event_date']} "
            f"{e['counterparty']} *¥{e['amount']:,}* "
            f"[{e.get('employee_name', '')}]"
        )
    if len(events) > 20:
        lines.append(f"\n_…他 {len(events) - 20} 件（`/export {year:04d}-{month:02d} csv` でCSV出力可）_")

    total = sum(e.get("amount", 0) for e in events)
    lines.append(f"\n*合計: ¥{total:,}*")

    client.chat_postMessage(channel=channel_id, text="\n".join(lines))
    logger.info(f"/list: {ym_label} {len(events)}件")


# ============================================================
# /edit コマンド（仕訳修正 — Slack モーダル）
# ============================================================

@app.command("/edit")
def handle_edit(ack, body, client, logger):
    """
    /edit [event_id]
    指定した管理IDの仕訳をモーダルで修正する。
    """
    ack()
    event_id   = body.get("text", "").strip().split()[0] if body.get("text", "").strip() else ""
    channel_id = body["channel_id"]
    trigger_id = body["trigger_id"]
    tenant     = _get_tenant(body.get("team_id", ""))
    tenant_id  = tenant["id"] if tenant else None

    if not event_id:
        client.chat_postMessage(channel=channel_id,
            text="❌ 使い方: `/edit T20260406-00001`")
        return

    evt = get_event_by_id(event_id, tenant_id)
    if not evt:
        client.chat_postMessage(channel=channel_id,
            text=f"❌ 管理ID `{event_id}` が見つかりません。")
        return

    from core.config import DEBIT_ACCOUNTS
    debit_options = [
        {"text": {"type": "plain_text", "text": acc}, "value": acc}
        for acc in DEBIT_ACCOUNTS
    ]
    current_account = evt.get("debit_account", "消耗品費")
    if current_account not in DEBIT_ACCOUNTS:
        current_account = "消耗品費"

    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "edit_event_modal",
            "private_metadata": f"{event_id}|{tenant_id or ''}",
            "title": {"type": "plain_text", "text": "仕訳を修正"},
            "submit": {"type": "plain_text", "text": "保存"},
            "close":  {"type": "plain_text", "text": "キャンセル"},
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*管理ID*: `{event_id}`　*ステータス*: {evt.get('status', '')}"}
                },
                {"type": "divider"},
                {
                    "type": "input", "block_id": "counterparty",
                    "label": {"type": "plain_text", "text": "取引先"},
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("counterparty", ""),
                        "placeholder": {"type": "plain_text", "text": "例：スターバックスコーヒー"}
                    }
                },
                {
                    "type": "input", "block_id": "event_date",
                    "label": {"type": "plain_text", "text": "発生日"},
                    "element": {
                        "type": "datepicker", "action_id": "value",
                        "initial_date": str(evt.get("event_date", ""))[:10]
                    }
                },
                {
                    "type": "input", "block_id": "amount",
                    "label": {"type": "plain_text", "text": "税込金額（円）"},
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": str(evt.get("amount", 0)),
                        "placeholder": {"type": "plain_text", "text": "例：1650"}
                    }
                },
                {
                    "type": "input", "block_id": "debit_account",
                    "label": {"type": "plain_text", "text": "借方科目"},
                    "element": {
                        "type": "static_select", "action_id": "value",
                        "options": debit_options,
                        "initial_option": {
                            "text": {"type": "plain_text", "text": current_account},
                            "value": current_account
                        }
                    }
                },
                {
                    "type": "input", "block_id": "debit_subsidiary",
                    "label": {"type": "plain_text", "text": "借方補助科目（任意）"},
                    "optional": True,
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("debit_subsidiary", "") or "",
                        "placeholder": {"type": "plain_text", "text": "例：タクシー代"}
                    }
                },
                {
                    "type": "input", "block_id": "invoice_number",
                    "label": {"type": "plain_text", "text": "T番号（任意）"},
                    "optional": True,
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("invoice_number", "") or "",
                        "placeholder": {"type": "plain_text", "text": "例：T1234567890123"}
                    }
                },
                {
                    "type": "input", "block_id": "purpose",
                    "label": {"type": "plain_text", "text": "用途・メモ（任意）"},
                    "optional": True,
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("purpose", "") or evt.get("memo", "") or "",
                        "placeholder": {"type": "plain_text", "text": "例：取引先との打合せ"},
                        "multiline": True
                    }
                },
            ],
        }
    )
    logger.info(f"/edit 開始: {event_id}")


@app.view("edit_event_modal")
def handle_edit_submit(ack, body, client, logger):
    """モーダル送信時の処理"""
    meta      = body["view"]["private_metadata"].split("|")
    event_id  = meta[0]
    tenant_id = meta[1] if len(meta) > 1 and meta[1] else None
    user_id   = body["user"]["id"]
    values    = body["view"]["state"]["values"]

    def _val(block_id):
        block = values.get(block_id, {})
        v_elem = block.get("value", {})
        if isinstance(v_elem, dict):
            return (v_elem.get("value")
                    or v_elem.get("selected_date")
                    or (v_elem.get("selected_option") or {}).get("value")
                    or "")
        return ""

    counterparty     = _val("counterparty")
    event_date       = _val("event_date")
    amount_str       = _val("amount")
    debit_account    = _val("debit_account")
    debit_subsidiary = _val("debit_subsidiary")
    invoice_number   = _val("invoice_number").strip() or None
    purpose          = _val("purpose")

    try:
        amount = int(amount_str.replace(",", "").replace("¥", "").strip())
    except (ValueError, AttributeError):
        amount = None

    # 承認済みの場合は金額変更を禁止
    evt_pre = get_event_by_id(event_id, tenant_id)
    if evt_pre and evt_pre.get("status") == "業務承認済" and amount is not None:
        if amount != evt_pre.get("amount"):
            ack(response_action="errors", errors={"amount": "承認済みのため金額を変更できません。取引先・科目・用途のみ修正可能です。"})
            return

    ack()

    from core.database import update_event
    fields = {}
    if counterparty:        fields["counterparty"]      = counterparty
    if event_date:          fields["event_date"]         = event_date
    if amount is not None:  fields["amount"]             = amount
    if debit_account:       fields["debit_account"]      = debit_account
    fields["debit_subsidiary"] = debit_subsidiary or ""
    fields["invoice_number"]   = invoice_number
    fields["has_invoice"]      = bool(invoice_number)
    fields["purpose"]          = purpose or ""
    fields["credit_account"]   = build_credit_account("")   # 旧フォーマット（括弧付き）を修正

    ok = update_event(event_id, tenant_id, fields)

    # 承認済みなら Sheets を再同期
    evt = get_event_by_id(event_id, tenant_id)
    sheets_synced = False
    if ok and evt and evt.get("status") == "業務承認済" and sheets:
        from core.accounting import JournalEntry
        updated_entry = JournalEntry(
            event_id          = evt["event_id"],
            event_date        = str(evt["event_date"]),
            counterparty      = evt["counterparty"],
            total_amount      = evt["amount"],
            taxable_10_amount = evt.get("taxable_10_amount", 0),
            tax_10_amount     = evt.get("tax_10_amount", 0),
            taxable_8_amount  = evt.get("taxable_8_amount", 0),
            tax_8_amount      = evt.get("tax_8_amount", 0),
            debit_account     = evt["debit_account"],
            debit_subsidiary  = evt.get("debit_subsidiary", ""),
            credit_account    = evt["credit_account"],
            invoice_number    = evt.get("invoice_number"),
            has_invoice       = bool(evt.get("has_invoice")),
            employee_name     = evt.get("employee_name", ""),
            status            = evt.get("status", ""),
            evidence_url      = evt.get("evidence_url", ""),
            purpose           = evt.get("purpose", ""),
        )
        sheets.update_journal_entry(updated_entry)
        sheets_synced = True
        logger.info(f"Sheets 再同期: {event_id}")

    editor_name = _get_employee_name(client, user_id)
    sync_note = "\n✅ Google Sheets を再同期しました" if sheets_synced else ""
    try:
        client.chat_postMessage(
            channel=user_id,
            text=(
                f"✏️ *仕訳を修正しました*\n\n"
                f"管理ID: `{event_id}`\n"
                f"取引先: {counterparty}\n"
                f"金額: {_fmt_yen(amount) if amount else '-'}\n"
                f"科目: {debit_account}\n"
                f"修正者: {editor_name}"
                f"{sync_note}"
            )
        )
    except Exception as dm_err:
        logger.warning(f"修正通知DM失敗: {dm_err}")

    _refresh_approval_card(client, event_id, tenant_id)
    _refresh_uploader_dm(client, event_id, tenant_id)
    logger.info(f"仕訳修正完了: {event_id} by {user_id}")


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


# ============================================================
# 用途・補助科目の入力（アップロード後の追加入力）
# ============================================================

@app.action("switch_to_kaigi_btn")
def handle_switch_to_kaigi(ack, body, client, logger):
    """飲食レシートを会議費に変更"""
    ack()
    raw = body["actions"][0]["value"]
    parts = raw.split("|", 1)
    event_id  = parts[0]
    tenant    = _get_tenant(body.get("team", {}).get("id", ""))
    tenant_id = tenant["id"] if tenant else (parts[1] if len(parts) > 1 else None)

    update_event(event_id, tenant_id, {"debit_account": "会議費", "debit_subsidiary": "会議飲食費"})
    _refresh_approval_card(client, event_id, tenant_id)
    _refresh_uploader_dm(client, event_id, tenant_id)
    client.chat_postMessage(
        channel=body["user"]["id"],
        text=f"✅ 借方科目を *会議費 / 会議飲食費* に変更しました。\n管理ID: `{event_id}`",
    )
    logger.info(f"科目変更 → 会議費: {event_id}")


@app.action("switch_to_settai_btn")
def handle_switch_to_settai(ack, body, client, logger):
    """飲食レシートを接待交際費に変更"""
    ack()
    raw = body["actions"][0]["value"]
    parts = raw.split("|", 1)
    event_id  = parts[0]
    tenant    = _get_tenant(body.get("team", {}).get("id", ""))
    tenant_id = tenant["id"] if tenant else (parts[1] if len(parts) > 1 else None)

    update_event(event_id, tenant_id, {"debit_account": "接待交際費", "debit_subsidiary": "接待飲食費"})
    _refresh_approval_card(client, event_id, tenant_id)
    _refresh_uploader_dm(client, event_id, tenant_id)
    client.chat_postMessage(
        channel=body["user"]["id"],
        text=f"✅ 借方科目を *接待交際費 / 接待飲食費* に変更しました。\n管理ID: `{event_id}`",
    )
    logger.info(f"科目変更 → 接待交際費: {event_id}")


@app.action("quick_edit_btn")
def handle_quick_edit_btn(ack, body, client, logger):
    """申請者DMの「内容を修正」ボタン → /edit と同じモーダルを開く"""
    ack()
    value     = body["actions"][0]["value"]
    event_id, tenant_id_str = value.split("|", 1)
    tenant_id = tenant_id_str or None
    trigger_id = body["trigger_id"]

    evt = get_event_by_id(event_id, tenant_id)
    if not evt:
        client.chat_postMessage(
            channel=body["user"]["id"],
            text=f"❌ 管理ID `{event_id}` が見つかりません。",
        )
        return

    from core.config import DEBIT_ACCOUNTS
    debit_options = [
        {"text": {"type": "plain_text", "text": acc}, "value": acc}
        for acc in DEBIT_ACCOUNTS
    ]
    current_account = evt.get("debit_account", "消耗品費")
    if current_account not in DEBIT_ACCOUNTS:
        current_account = "消耗品費"

    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "edit_event_modal",
            "private_metadata": f"{event_id}|{tenant_id or ''}",
            "title": {"type": "plain_text", "text": "仕訳を修正"},
            "submit": {"type": "plain_text", "text": "保存"},
            "close":  {"type": "plain_text", "text": "キャンセル"},
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*管理ID*: `{event_id}`　*ステータス*: {evt.get('status', '')}"}
                },
                {"type": "divider"},
                {
                    "type": "input", "block_id": "counterparty",
                    "label": {"type": "plain_text", "text": "取引先"},
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("counterparty", ""),
                        "placeholder": {"type": "plain_text", "text": "例：スターバックスコーヒー"}
                    }
                },
                {
                    "type": "input", "block_id": "event_date",
                    "label": {"type": "plain_text", "text": "発生日"},
                    "element": {
                        "type": "datepicker", "action_id": "value",
                        "initial_date": str(evt.get("event_date", ""))[:10]
                    }
                },
                {
                    "type": "input", "block_id": "amount",
                    "label": {"type": "plain_text", "text": "税込金額（円）"},
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": str(evt.get("amount", 0)),
                        "placeholder": {"type": "plain_text", "text": "例：1650"}
                    }
                },
                {
                    "type": "input", "block_id": "debit_account",
                    "label": {"type": "plain_text", "text": "借方科目"},
                    "element": {
                        "type": "static_select", "action_id": "value",
                        "options": debit_options,
                        "initial_option": {
                            "text": {"type": "plain_text", "text": current_account},
                            "value": current_account
                        }
                    }
                },
                {
                    "type": "input", "block_id": "debit_subsidiary",
                    "label": {"type": "plain_text", "text": "借方補助科目（任意）"},
                    "optional": True,
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("debit_subsidiary", "") or "",
                        "placeholder": {"type": "plain_text", "text": "例：タクシー代"}
                    }
                },
                {
                    "type": "input", "block_id": "invoice_number",
                    "label": {"type": "plain_text", "text": "T番号（任意）"},
                    "optional": True,
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("invoice_number", "") or "",
                        "placeholder": {"type": "plain_text", "text": "例：T1234567890123"}
                    }
                },
                {
                    "type": "input", "block_id": "purpose",
                    "label": {"type": "plain_text", "text": "用途・メモ（任意）"},
                    "optional": True,
                    "element": {
                        "type": "plain_text_input", "action_id": "value",
                        "initial_value": evt.get("purpose", "") or evt.get("memo", "") or "",
                        "placeholder": {"type": "plain_text", "text": "例：取引先との打合せ"},
                        "multiline": True
                    }
                },
            ],
        }
    )


@app.action("input_purpose_btn")
def handle_input_purpose_btn(ack, body, client, logger):
    """アップロード後の「用途・補助科目を入力」ボタン"""
    ack()
    raw = body["actions"][0]["value"]          # "event_id|tenant_id"
    parts = raw.split("|", 1)
    event_id  = parts[0]
    tenant_id = parts[1] if len(parts) > 1 else None

    tenant = _get_tenant(body.get("team", {}).get("id", ""))
    if tenant:
        tenant_id = tenant["id"]

    evt = get_event_by_id(event_id, tenant_id) or {}

    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "purpose_modal",
                "private_metadata": f"{event_id}|{tenant_id}",
                "title": {"type": "plain_text", "text": "用途・補助科目の入力"},
                "submit": {"type": "plain_text", "text": "💾 保存"},
                "close": {"type": "plain_text", "text": "閉じる"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{evt.get('counterparty', '')}* "
                                f"{_fmt_yen(evt.get('amount', 0))} / "
                                f"{evt.get('debit_account', '')}"
                            ),
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "purpose_block",
                        "label": {"type": "plain_text", "text": "用途（DM入力欄）"},
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "purpose",
                            "placeholder": {"type": "plain_text", "text": "例: 〇〇社との打合せ、△△の接待、□□の会議"},
                            "initial_value": evt.get("purpose", "") or "",
                        },
                        "optional": True,
                    },
                    {
                        "type": "input",
                        "block_id": "subsidiary_block",
                        "label": {"type": "plain_text", "text": "借方補助科目（K列）"},
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "debit_subsidiary",
                            "initial_value": evt.get("debit_subsidiary", "") or "",
                        },
                        "optional": True,
                    },
                ],
            },
        )
    except Exception as e:
        logger.error(f"用途モーダル開発エラー: {e}")


@app.view("purpose_modal")
def handle_purpose_modal(ack, body, client, logger):
    """用途・補助科目モーダルの送信処理"""
    ack()
    meta = body["view"]["private_metadata"].split("|", 1)
    event_id  = meta[0]
    tenant_id = meta[1] if len(meta) > 1 and meta[1] else None

    tenant = _get_tenant(body.get("team", {}).get("id", ""))
    if tenant:
        tenant_id = tenant["id"]

    vals = body["view"]["state"]["values"]
    purpose          = (vals.get("purpose_block", {}).get("purpose", {}).get("value") or "").strip()
    debit_subsidiary = (vals.get("subsidiary_block", {}).get("debit_subsidiary", {}).get("value") or "").strip()

    fields = {}
    if purpose:
        fields["purpose"] = purpose
    if debit_subsidiary:
        fields["debit_subsidiary"] = debit_subsidiary
    if fields:
        update_event(event_id, tenant_id, fields)

    # 承認済の場合は Sheets を再同期（K・P列を反映）
    try:
        evt = get_event_by_id(event_id, tenant_id)
        if evt and evt.get("status") == "業務承認済" and sheets:
            from core.accounting import JournalEntry
            sync_entry = JournalEntry(
                event_id          = evt["event_id"],
                event_date        = str(evt["event_date"]),
                counterparty      = evt["counterparty"],
                total_amount      = evt["amount"],
                taxable_10_amount = evt.get("taxable_10_amount", 0),
                tax_10_amount     = evt.get("tax_10_amount", 0),
                taxable_8_amount  = evt.get("taxable_8_amount", 0),
                tax_8_amount      = evt.get("tax_8_amount", 0),
                debit_account     = evt["debit_account"],
                debit_subsidiary  = debit_subsidiary or evt.get("debit_subsidiary", ""),
                credit_account    = evt["credit_account"],
                invoice_number    = evt.get("invoice_number"),
                has_invoice       = bool(evt.get("has_invoice")),
                employee_name     = evt.get("employee_name", ""),
                status            = "業務承認済",
                evidence_url      = evt.get("evidence_url", ""),
                purpose           = purpose or evt.get("purpose", ""),
            )
            sheets.update_journal_entry(sync_entry)
            logger.info(f"Sheets 再同期完了 (purpose更新): {event_id}")
    except Exception as e:
        logger.warning(f"Sheets 再同期スキップ: {e}")

    _refresh_approval_card(client, event_id, tenant_id)
    _refresh_uploader_dm(client, event_id, tenant_id)

    # 申請者に保存完了を通知
    user_id = body["user"]["id"]
    saved_items = []
    if purpose:
        saved_items.append(f"用途: {purpose}")
    if debit_subsidiary:
        saved_items.append(f"借方補助科目: {debit_subsidiary}")
    notify_text = "✅ " + " / ".join(saved_items) + " を保存しました。" if saved_items else "（変更なし）"
    try:
        client.chat_postMessage(channel=user_id, text=notify_text)
    except Exception:
        pass


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
        event_date = datetime.now().strftime("%Y-%m-%d")
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
