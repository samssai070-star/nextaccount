"""NextAccount v2 — CSV export handler with date range selection"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
user_date_selections = {}


def setup_csv_commands(app, get_tenant_fn, logger_obj):
    """Setup CSV export commands"""
    global user_date_selections
    log = logger_obj

    @app.command("/csv")
    def handle_csv(ack, body, client):
        ack()
        channel_id = body["channel_id"]
        user_id = body["user_id"]
        
        today = datetime.now()
        start = today.replace(day=1).strftime("%Y-%m-%d")
        last = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        end = last.strftime("%Y-%m-%d")
        
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "📊 CSV エクスポート"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*期間を選択してください:*"}},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "開始日付:"},
                "accessory": {
                    "type": "datepicker",
                    "action_id": "csv_start_date",
                    "initial_date": start
                }
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "終了日付:"},
                "accessory": {
                    "type": "datepicker",
                    "action_id": "csv_end_date",
                    "initial_date": end
                }
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": "*形式を選択してください:*"}},
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "🧾 弥生"}, "action_id": "csv_yayoi", "style": "primary"},
                    {"type": "button", "text": {"type": "plain_text", "text": "📱 freee"}, "action_id": "csv_freee"},
                    {"type": "button", "text": {"type": "plain_text", "text": "💰 マネーフォワード"}, "action_id": "csv_mf"},
                    {"type": "button", "text": {"type": "plain_text", "text": "📄 汎用"}, "action_id": "csv_csv"},
                ]
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": "*エンタープライズ向け:*"}},
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "🏢 勘定奉行"}, "action_id": "csv_kanjo"},
                    {"type": "button", "text": {"type": "plain_text", "text": "🏛️ PCA会計"}, "action_id": "csv_pca"},
                    {"type": "button", "text": {"type": "plain_text", "text": "📋 TKC"}, "action_id": "csv_tkc"},
                ]
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": "*会計事務所向け:*"}},
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "🔵 JDL"}, "action_id": "csv_jdl"},
                    {"type": "button", "text": {"type": "plain_text", "text": "⚡ MJS"}, "action_id": "csv_mjs"},
                ]
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": "*クラウド会計:*"}},
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "🌐 クラウド円簿"}, "action_id": "csv_yenbo"},
                ]
            }
        ]
        
        client.chat_postMessage(channel=channel_id, text="CSV エクスポート", blocks=blocks)
        user_date_selections[user_id] = {"start": start, "end": end}
        log.info(f"CSV menu: {user_id}")

    @app.action("csv_start_date")
    def handle_start(ack, body, client):
        ack()
        uid = body["user"]["id"]
        date = body["actions"][0]["selected_date"]
        if uid not in user_date_selections:
            user_date_selections[uid] = {}
        user_date_selections[uid]["start"] = date

    @app.action("csv_end_date")
    def handle_end(ack, body, client):
        ack()
        uid = body["user"]["id"]
        date = body["actions"][0]["selected_date"]
        if uid not in user_date_selections:
            user_date_selections[uid] = {}
        user_date_selections[uid]["end"] = date

    for fmt_code, fmt_name in [
        ("csv_yayoi", "yayoi"), ("csv_freee", "freee"), ("csv_mf", "mf"), ("csv_csv", "csv"),
        ("csv_kanjo", "kanjo_ahra"), ("csv_pca", "pca"), ("csv_tkc", "tkc"),
        ("csv_jdl", "jdl"), ("csv_mjs", "mjs"), ("csv_yenbo", "yenbo")
    ]:
        def make_handler(fmt):
            def handler(ack, body, client):
                _export(ack, body, client, log, fmt, get_tenant_fn)
            return handler
        
        app.action(fmt_code)(make_handler(fmt_name))

    log.info("✅ CSV export (10 formats + date range)")


def _export(ack, body, client, log, fmt, get_tenant_fn):
    """Handle CSV export"""
    ack()
    cid = body.get("channel", {}).get("id") or body.get("channel_id")
    uid = body.get("user", {}).get("id") or body.get("user_id")
    
    dates = user_date_selections.get(uid, {})
    start = dates.get("start", "")
    end = dates.get("end", "")
    
    if not start or not end:
        client.chat_postMessage(channel=cid, text="開始日付と終了日付を選択してください。")
        return
    
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        label = start_dt.strftime("%Y/%m/%d") + " ～ " + end_dt.strftime("%Y/%m/%d")
        
        tid = body.get("team", {}).get("id") or body.get("team_id", "")
        tenant = get_tenant_fn(tid)
        tenant_id = tenant["id"] if tenant else None
        
        from core.database import _get_conn
        events = []
        with _get_conn(tenant_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM accounting_events WHERE tenant_id=%s AND event_date>=%s AND event_date<=%s AND status=%s ORDER BY event_date",
                    (tenant_id, start, end, "業務承認済")
                )
                cols = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    evt = dict(zip(cols, row))
                    if "amount" not in evt or evt["amount"] is None:
                        evt["amount"] = sum([
                            int(evt.get(k, 0) or 0) for k in 
                            ["taxable_10_amount", "tax_10_amount", "taxable_8_amount", "tax_8_amount"]
                        ])
                    events.append(evt)
        
        if not events:
            client.chat_postMessage(channel=cid, text=f"📭 {label} の承認済み仕訳が見つかりません。")
            return
        
        fmt_funcs = {
            "yayoi": ("core.yayoi_export", "build_yayoi_csv", "yayoi", "弥生"),
            "yenbo": ("core.yayoi_export", "build_yenbo_csv", "yenbo", "クラウド円簿"),
            "freee": ("core.csv_export", "build_freee_csv", "freee", "freee"),
            "mf": ("core.csv_export", "build_mf_csv", "mf", "MF"),
            "csv": ("core.csv_export", "build_generic_csv", "csv", "汎用"),
            "kanjo_ahra": ("core.multi_software_export", "build_kanjo_ahra_csv", "kanjo", "勘定奉行"),
            "pca": ("core.multi_software_export", "build_pca_csv", "pca", "PCA"),
            "tkc": ("core.multi_software_export", "build_tkc_csv", "tkc", "TKC"),
            "jdl": ("core.multi_software_export", "build_jdl_csv", "jdl", "JDL"),
            "mjs": ("core.multi_software_export", "build_mjs_csv", "mjs", "MJS")
        }
        
        mod, func, fname, label_jp = fmt_funcs[fmt]
        exec(f"from {mod} import {func}")
        csv_bytes = eval(f"{func}(events)")
        
        filename = fname + "_" + start_dt.strftime("%Y%m%d") + "_" + end_dt.strftime("%Y%m%d") + ".csv"
        client.files_upload_v2(
            channel=cid,
            content=csv_bytes,
            filename=filename,
            title=f"{label_jp} {label} ({len(events)}件)",
            initial_comment=f"📊 *{label} {label_jp}形式*\n件数: {len(events)}件"
        )
        log.info(f"✅ Export: {filename}")
    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)
        client.chat_postMessage(channel=cid, text=f"❌ エラー: {e}")

