from __future__ import annotations
import os, sys, threading, logging, smtplib
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout,
)
logger = logging.getLogger("nextaccount.main")

flask_app = Flask(__name__,
    static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    static_url_path='/static')
_start_time = datetime.now()

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
    return jsonify({
        "status": "ok",
        "service": "nextaccount-v2",
        "uptime_seconds": uptime,
        "env": os.environ.get("ENVIRONMENT", "development")
    }), 200


@flask_app.route("/", methods=["GET"])
def root():
    return send_from_directory(flask_app.static_folder, "index.html")


@flask_app.route("/<path:filename>", methods=["GET"])
def serve_static(filename):
    static_dir = flask_app.static_folder
    file_path = os.path.join(static_dir, filename)
    if os.path.isfile(file_path) and filename.endswith('.html'):
        return send_from_directory(static_dir, filename)
    html_path = os.path.join(static_dir, filename + '.html')
    if os.path.isfile(html_path):
        return send_from_directory(static_dir, filename + '.html')
    return jsonify({"error": "Not found"}), 404


def send_email(to_email, subject, body, from_email=None, is_html=False):
    smtp_server = os.environ.get("SMTP_SERVER") or os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = from_email or os.environ.get("MAIL_FROM") or os.environ.get("CONTACT_FROM_EMAIL", "support@nextaccount.jp")

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
    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        subject = data.get("subject", "").strip()
        message = data.get("message", "").strip()

        if not all([name, email, subject, message]):
            return jsonify({"error": "すべてのフィールドが必須です"}), 400

        if "@" not in email:
            return jsonify({"error": "有効なメールアドレスを入力してください"}), 400

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
        send_email(email, "NextAccount - お問い合わせ確認", user_body)

        support_email = os.environ.get("SUPPORT_EMAIL", "support@nextaccount.jp")
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
        send_email(support_email, f"[NextAccount] 新しいお問い合わせ - {subject}", support_body)

        logger.info(f"Contact form submitted - Name: {name}, Email: {email}, Subject: {subject}")
        return jsonify({"success": True, "message": "お問い合わせを送信しました"}), 200

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
        from bot.slack_handler import start as slack_start
        logger.info("Slack Bot 起動完了 — イベント待機中")
        slack_start()
    except Exception as e:
        logger.error(f"Slack Bot 起動失敗: {e}", exc_info=True)


def main():
    logger.info("=" * 60)
    logger.info("NextAccount v2 起動中")
    logger.info(f"  環境 : {os.environ.get('ENVIRONMENT', 'development')}")
    logger.info(f"  TZ   : {os.environ.get('TZ', 'UTC')}")
    logger.info("=" * 60)
    _write_google_key_from_env()
    bot_thread = threading.Thread(target=run_slack_bot, name="slack-bot", daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Flask サーバー起動: 0.0.0.0:{port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
