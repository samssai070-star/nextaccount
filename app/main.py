from __future__ import annotations
import os, sys, signal, threading, logging, smtplib
from datetime import datetime
from flask import Flask, jsonify, request, redirect, send_from_directory
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

# Setup Flask app with static files
flask_app = Flask(__name__,
    static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    static_url_path='/static')
_start_time = datetime.now()
_bot_healthy = threading.Event()

# Register API blueprints
try:
    from api.auth import auth_bp
    from api.setup import setup_bp
    from api.slack_oauth import slack_bp
    from api.dashboard import dashboard_bp

    flask_app.register_blueprint(auth_bp)
    flask_app.register_blueprint(setup_bp)
    flask_app.register_blueprint(slack_bp)
    flask_app.register_blueprint(dashboard_bp)
    logger.info("API blueprints registered successfully")
except ImportError as e:
    logger.warning(f"Failed to import API blueprints: {e}")

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
    return send_from_directory(flask_app.static_folder, "index.html")

@flask_app.route("/<path:filename>", methods=["GET"])
def serve_static(filename):
    """Serve static HTML files"""
    static_dir = flask_app.static_folder
    file_path = os.path.join(static_dir, filename)
    if os.path.isfile(file_path) and filename.endswith('.html'):
        return send_from_directory(static_dir, filename)
    return jsonify({"error": "Not found"}), 404

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


@flask_app.route("/slack/install", methods=["GET"])
def slack_install():
    from core.slack_oauth import generate_oauth_url
    plan = request.args.get("plan", "micro")
    try:
        redirect_uri = request.host_url.rstrip("/") + "/slack/oauth/callback"
        url = generate_oauth_url(plan=plan, redirect_uri=redirect_uri)
        return redirect(url)
    except Exception as e:
        logger.error(f"OAuth URL生成失敗: {e}")
        return f"設定エラー: {e}", 500


@flask_app.route("/slack/oauth/callback", methods=["GET"])
def slack_oauth_callback():
    from core.slack_oauth import exchange_code
    from core.database import create_tenant
    code  = request.args.get("code", "")
    state = request.args.get("state", "")
    error = request.args.get("error", "")

    if error:
        logger.warning(f"OAuth キャンセル: {error}")
        return redirect("https://nextaccount.jp/#pricing")

    if not code:
        return "不正なリクエスト", 400

    redirect_uri = request.host_url.rstrip("/") + "/slack/oauth/callback"
    result = exchange_code(code=code, state=state, redirect_uri=redirect_uri)

    if not result.get("ok"):
        logger.error(f"OAuth 失敗: {result.get('error')}")
        return f"認証に失敗しました: {result.get('error')}", 400

    team_id   = result["team_id"]
    bot_token = result["bot_token"]
    plan      = result.get("plan", "micro")

    try:
        tenant = create_tenant(slack_team_id=team_id, slack_bot_token=bot_token)
        logger.info(f"テナント登録完了: {team_id} plan={plan}")
    except Exception as e:
        logger.error(f"テナント登録失敗: {e}")
        return f"登録エラー: {e}", 500

    checkout_url = f"/checkout/{plan}?tenant_id={tenant['id']}&team_id={team_id}"
    return redirect(checkout_url)


def send_email(to_email, subject, body, from_email=None, is_html=False):
    """Send email using SMTP"""
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = from_email or os.environ.get("CONTACT_FROM_EMAIL", "noreply@nextaccount.jp")

    if not smtp_server or not smtp_user or not smtp_password:
        logger.warning("SMTP settings not configured, email not sent")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email

        if is_html:
            msg.attach(MIMEText(body, 'html', 'utf-8'))
        else:
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        logger.info(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


@flask_app.route("/api/send-contact", methods=["POST"])
def send_contact():
    """Handle contact form submission"""
    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        subject = data.get("subject", "").strip()
        message = data.get("message", "").strip()

        if not all([name, email, subject, message]):
            return jsonify({"error": "すべてのフィールドが必須です"}), 400

        # Validate email format
        if "@" not in email:
            return jsonify({"error": "有効なメールアドレスを入力してください"}), 400

        # Send confirmation email to user
        user_subject = "NextAccount - お問い合わせ確認"
        user_body = f"""いつもNextAccountをご利用いただきありがとうございます。

お問い合わせの内容を受け取りました。
確認のため、ご送信いただいた内容を下記に記載します。

【お名前】
{name}

【メールアドレス】
{email}

【件名】
{subject}

【メッセージ】
{message}

ご返答させていただくまでお待ちください。
ご不明な点がございましたら、お気軽にお問い合わせください。

---
NextAccount サポートチーム
support@nextaccount.jp
"""
        send_email(email, user_subject, user_body)

        # Send notification email to support team
        support_email = os.environ.get("SUPPORT_EMAIL", "support@nextaccount.jp")
        support_subject = f"[NextAccount] 新しいお問い合わせ - {subject}"
        support_body = f"""新しいお問い合わせを受け取りました。

【送信者】
{name} ({email})

【件名】
{subject}

【メッセージ】
{message}

【受信時刻】
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        send_email(support_email, support_subject, support_body)

        logger.info(f"Contact form submitted - Name: {name}, Email: {email}, Subject: {subject}")

        return jsonify({
            "success": True,
            "message": "お問い合わせを送信しました"
        }), 200

    except Exception as e:
        logger.error(f"Contact form error: {e}", exc_info=True)
        return jsonify({"error": "お問い合わせの送信に失敗しました"}), 500


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
