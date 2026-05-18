"""
NextAccount v2 — bot/transportation_handler.py
交通費申請メッセージハンドラ
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def setup_transportation_handlers(app, get_tenant_fn, logger_obj):
    """交通費関連のハンドラを登録"""
    log = logger_obj

    @app.event("message")
    def handle_transportation_message(body, say, client):
        """
        メッセージで「交通費 新宿 渋谷 250」形式の申請を処理
        """
        text = body.get("text", "").strip()
        if not text.startswith("交通費"):
            return
        
        user_id = body.get("user")
        channel_id = body["channel"]
        team_id = body.get("team_id", "")
        
        if not user_id:
            return
        
        # テキストをパース
        parts = text.split()
        if len(parts) < 4:
            say("❌ 使い方: `交通費 新宿 渋谷 250`")
            return
        
        from_station = parts[1]
        to_station = parts[2]
        try:
            claimed_amount = int(parts[3])
        except ValueError:
            say(f"❌ 金額が数値ではありません: {parts[3]}")
            return
        
        try:
            tenant = get_tenant_fn(team_id)
            tenant_id = tenant["id"] if tenant else None
            
            # ユーザーの定期券情報を取得
            from core.database import get_commute_ticket
            commute_ticket = get_commute_ticket(user_id, tenant_id)
            
            # 定期区間との重複を計算
            from core.transportation import calculate_overlap_deduction, validate_transportation_price
            
            if commute_ticket:
                overlap_info = calculate_overlap_deduction(
                    commute_ticket["from_station"],
                    commute_ticket["to_station"],
                    from_station,
                    to_station
                )
                final_amount = overlap_info.get("final_reimbursement") or claimed_amount
                overlap_msg = overlap_info.get("message", "")
            else:
                final_amount = claimed_amount
                overlap_msg = "（定期券未登録）"
            
            # 経済合理性チェック
            is_valid, price_msg = validate_transportation_price(from_station, to_station, claimed_amount)
            
            # Slack ユーザー情報取得
            user_info = client.users_info(user=user_id)
            employee_name = user_info["user"].get("real_name") or user_info["user"].get("name")
            
            # 承認カードを表示
            approval_blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🚆 交通費申請"}
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*申請者:*\n{employee_name}"},
                        {"type": "mrkdwn", "text": f"*区間:*\n{from_station} → {to_station}"},
                        {"type": "mrkdwn", "text": f"*申告額:*\n¥{claimed_amount:,}"},
                        {"type": "mrkdwn", "text": f"*報告額:*\n¥{final_amount:,}"}
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*経済合理性:* {price_msg}\n*定期区間:* {overlap_msg}"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "✅ 承認"}, "value": "approve", "style": "primary"},
                        {"type": "button", "text": {"type": "plain_text", "text": "❌ 却下"}, "value": "reject"}
                    ]
                }
            ]
            
            say(blocks=approval_blocks)
            log.info(f"交通費申請: {employee_name} ({from_station}→{to_station}) ¥{claimed_amount} → ¥{final_amount}")
            
        except Exception as e:
            log.error(f"交通費処理エラー: {e}", exc_info=True)
            say(f"❌ エラー: {e}")

    log.info("✅ Transportation handlers registered")

