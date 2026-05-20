from __future__ import annotations
import os, sys, signal, threading, logging
from datetime import datetime
from flask import Flask, jsonify, request, redirect

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout,
)
logger = logging.getLogger("nextaccount.main")

REQUIRED_VARS = ["SLACK_BOT_TOKEN","SLACK_APP_TOKEN","DATABASE_URL",
                 "GOOGLE_APPLICATION_CREDENTIALS","GOOGLE_SHEET_ID"]

def check_env():
    return [v for v in REQUIRED_VARS if not os.environ.get(v)]

flask_app = Flask(__name__)
_start_time = datetime.now()
_bot_healthy = threading.Event()

@flask_app.route("/health", methods=["GET"])
def health():
    uptime = int((datetime.now() - _start_time).total_seconds())
    ok = _bot_healthy.is_set()
    return jsonify({"status": "ok" if ok else "starting",
                    "service": "nextaccount-v2",
                    "uptime_seconds": uptime,
                    "env": os.environ.get("ENVIRONMENT","development")}), 200 if ok else 503

@flask_app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "NextAccount v2", "version": "2.0.0"}), 200

@flask_app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    import hmac, hashlib
    from core.stripe_billing import handle_webhook
    import psycopg2

    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        result = handle_webhook(payload, sig_header)
    except Exception as e:
        logger.error(f"Webhook処理失敗: {e}")
        return jsonify({"error": str(e)}), 400

    event_type = result.get("event_type", "")

    if event_type == "checkout.session.completed":
        tenant_id       = result.get("tenant_id", "")
        customer_id     = result.get("customer_id", "")
        subscription_id = result.get("subscription_id", "")
        plan            = result.get("plan", "")
        if tenant_id:
            try:
                conn = psycopg2.connect(os.environ.get("DATABASE_URL", ""))
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tenants SET
                            stripe_customer_id = %s,
                            stripe_subscription_id = %s,
                            stripe_price_id = %s,
                            billing_status = \'trial\',
                            trial_ends_at = NOW() + INTERVAL \'30 days\',
                            updated_at = NOW()
                        WHERE id = %s
                    """, (customer_id, subscription_id, plan, tenant_id))
                conn.close()
                logger.info(f"Stripe連携完了: tenant={tenant_id} plan={plan}")
            except Exception as e:
                logger.error(f"DB更新失敗: {e}")

    elif event_type == "customer.subscription.deleted":
        tenant_id = result.get("tenant_id", "")
        if tenant_id:
            try:
                conn = psycopg2.connect(os.environ.get("DATABASE_URL", ""))
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE tenants SET billing_status=\'canceled\', updated_at=NOW() WHERE id=%s",
                        (tenant_id,)
                    )
                conn.close()
            except Exception as e:
                logger.error(f"キャンセル更新失敗: {e}")

    elif event_type == "invoice.payment_failed":
        logger.warning(f"支払い失敗: {result}")

    elif event_type == "invoice.payment_succeeded":
        tenant_id = result.get("tenant_id", "")
        amount_paid = result.get("amount_paid", 0)
        logger.info(f"支払い成功: tenant={tenant_id} amount={amount_paid}")
        if tenant_id:
            try:
                conn = psycopg2.connect(os.environ.get("DATABASE_URL", ""))
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE tenants SET billing_status='active', updated_at=NOW() WHERE id=%s",
                        (tenant_id,)
                    )
                conn.close()
            except Exception as e:
                logger.error(f"支払い成功DB更新失敗: {e}")

    return jsonify({"status": "ok"}), 200


@flask_app.route("/checkout/<plan>", methods=["GET"])
def checkout(plan):
    from flask import redirect as flask_redirect
    from core.stripe_billing import create_checkout_session
    tenant_id     = request.args.get("tenant_id", "demo")
    slack_team_id = request.args.get("team_id", "")
    base_url      = "https://nextaccount.jp"
    try:
        url = create_checkout_session(
            plan=plan,
            tenant_id=tenant_id,
            slack_team_id=slack_team_id,
            success_url=f"{base_url}/success",
            cancel_url=f"{base_url}/#pricing",
        )
        return flask_redirect(url)
    except Exception as e:
        logger.error(f"Checkout作成失敗: {e}")
        return f"エラー: {e}", 400


@flask_app.route("/success", methods=["GET"])
def success():
    return """
    <html><body style="font-family:sans-serif;text-align:center;padding:4rem">
    <h1>🎉 ありがとうございます！</h1>
    <p>30日間の無料トライアルが開始されました。<br>
    Slackの設定方法をメールでご案内します。</p>
    <a href="https://nextaccount.jp">トップページへ戻る</a>
    </body></html>
    """, 200



def _write_google_key_from_env():
    key_json = os.environ.get("GOOGLE_KEY_JSON", "")
    if not key_json:
        return
    target = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/google_key.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        f.write(key_json)
    os.chmod(target, 0o600)
    logger.info(f"GOOGLE_KEY_JSON → {target} に書き出しました")

def run_slack_bot():
    try:
        logger.info("Slack Bot 初期化中...")
        from core.database import init_database
        init_database()
        logger.info("DB 初期化完了")
        from bot.slack_handler import start as slack_start
        _bot_healthy.set()
        logger.info("Slack Bot 起動完了 — イベント待機中")
        slack_start()
    except Exception as e:
        logger.critical(f"Slack Bot 起動失敗: {e}", exc_info=True)
        _bot_healthy.clear()
        os.kill(os.getpid(), signal.SIGTERM)

def main():
    logger.info("=" * 60)
    logger.info("NextAccount v2 起動中")
    logger.info(f"  環境 : {os.environ.get('ENVIRONMENT', 'development')}")
    logger.info(f"  TZ   : {os.environ.get('TZ', 'UTC')}")
    logger.info("=" * 60)
    _write_google_key_from_env()
    missing = check_env()
    if missing:
        logger.warning(f"未設定の環境変数: {', '.join(missing)}")
    bot_thread = threading.Thread(target=run_slack_bot, name="slack-bot", daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Flask ヘルスチェックサーバー起動: 0.0.0.0:{port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
