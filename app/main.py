from __future__ import annotations
import os, sys, threading, logging, smtplib, time
from datetime import datetime, timedelta, timezone
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
    from api.billing import billing_bp, ensure_billing_columns
    from api.clients import clients_bp
    from api.org import org_bp

    flask_app.register_blueprint(auth_bp)
    flask_app.register_blueprint(setup_bp)
    flask_app.register_blueprint(slack_bp)
    flask_app.register_blueprint(dashboard_bp)
    flask_app.register_blueprint(billing_bp)
    flask_app.register_blueprint(clients_bp)
    flask_app.register_blueprint(org_bp)
    ensure_billing_columns()
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


@flask_app.route("/favicon.svg")
def favicon():
    return send_from_directory(flask_app.static_folder, "favicon.svg")


STATIC_EXTENSIONS = {'.html', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.css', '.js', '.woff', '.woff2', '.ttf'}

@flask_app.route("/<path:filename>", methods=["GET"])
def serve_static(filename):
    static_dir = flask_app.static_folder
    file_path = os.path.join(static_dir, filename)
    ext = os.path.splitext(filename)[1].lower()
    if os.path.isfile(file_path) and ext in STATIC_EXTENSIONS:
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


def _get_org_admin_email(conn, org_id: int):
    """組織の管理者メールアドレスを取得"""
    from api.helpers import get_db_cursor
    cur = get_db_cursor(conn)
    cur.execute(
        "SELECT email, full_name FROM users WHERE organization_id=%s AND is_admin=TRUE LIMIT 1",
        (org_id,)
    )
    return cur.fetchone()


@flask_app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    try:
        payload = request.get_data()
        sig_header = request.headers.get("Stripe-Signature", "")

        from core.stripe_billing import handle_webhook
        event = handle_webhook(payload, sig_header)

        event_type = event.get("event_type", "")

        if event_type == "checkout.session.completed":
            try:
                org_id = int(event.get("org_id", 0))
            except (ValueError, TypeError):
                org_id = 0
            plan = event.get("plan", "")
            customer_id = event.get("customer_id", "")
            subscription_id = event.get("subscription_id", "")
            mode = event.get("mode", "")

            if org_id:
                from api.helpers import get_db_connection
                conn = get_db_connection()
                user = _get_org_admin_email(conn, org_id)
                from api.helpers import get_db_cursor
                cur = get_db_cursor(conn)
                if mode == "setup":
                    cur.execute(
                        """UPDATE organizations
                           SET stripe_customer_id=%s, updated_at=CURRENT_TIMESTAMP
                           WHERE id=%s""",
                        (customer_id, org_id)
                    )
                    conn.commit()
                    conn.close()
                    logger.info(f"Card registered: org_id={org_id}")
                    if user:
                        _send_card_registered_email(user["email"], user["full_name"])
                else:
                    cur.execute(
                        """UPDATE organizations
                           SET subscription_status='active', plan=%s,
                               stripe_customer_id=%s, stripe_subscription_id=%s,
                               updated_at=CURRENT_TIMESTAMP
                           WHERE id=%s""",
                        (plan, customer_id, subscription_id, org_id)
                    )
                    conn.commit()
                    conn.close()
                    logger.info(f"Stripe checkout completed: org_id={org_id} plan={plan}")

        elif event_type == "invoice.payment_succeeded":
            subscription_id = event.get("subscription_id", "")
            invoice_pdf = event.get("invoice_pdf", "")
            invoice_url = event.get("invoice_url", "")
            amount_paid = event.get("amount_paid", 0)
            if subscription_id and invoice_pdf:
                from api.helpers import get_db_connection, get_db_cursor
                conn = get_db_connection()
                cur = get_db_cursor(conn)
                cur.execute(
                    """SELECT o.id, u.email, u.full_name, o.plan
                       FROM organizations o
                       JOIN users u ON u.organization_id = o.id AND u.is_admin = TRUE
                       WHERE o.stripe_subscription_id=%s LIMIT 1""",
                    (subscription_id,)
                )
                row = cur.fetchone()
                conn.close()
                if row:
                    _send_invoice_email(
                        row["email"], row["full_name"],
                        row["plan"], amount_paid, invoice_pdf, invoice_url
                    )

        elif event_type == "customer.subscription.trial_will_end":
            org_id_str = event.get("org_id", "")
            trial_end_ts = event.get("trial_end")
            try:
                org_id = int(org_id_str)
            except (ValueError, TypeError):
                org_id = 0
            if org_id and trial_end_ts:
                from api.helpers import get_db_connection
                conn = get_db_connection()
                user = _get_org_admin_email(conn, org_id)
                conn.close()
                if user:
                    trial_end_dt = datetime.fromtimestamp(trial_end_ts)
                    _send_trial_ending_email(user["email"], user["full_name"], trial_end_dt, days_left=7)

        elif event_type == "customer.subscription.deleted":
            subscription_id = event.get("subscription_id", "")
            if subscription_id:
                from api.helpers import get_db_connection, get_db_cursor
                conn = get_db_connection()
                cur = get_db_cursor(conn)
                cur.execute(
                    """UPDATE organizations
                       SET subscription_status='canceled', updated_at=CURRENT_TIMESTAMP
                       WHERE stripe_subscription_id=%s""",
                    (subscription_id,)
                )
                conn.commit()
                conn.close()
                logger.info(f"Subscription canceled: {subscription_id}")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        return jsonify({"error": str(e)}), 400


def _send_card_registered_email(to_email: str, full_name: str):
    subject = "【NextAccount】支払い方法の登録が完了しました"
    body = f"""{full_name} 様

NextAccount をご利用いただきありがとうございます。

クレジットカードの登録が正常に完了しました。
今後のプラン変更時にカード情報の再入力は不要です。

ご不明な点がございましたら、サポートまでお問い合わせください。

---
NextAccount サポートチーム
support@nextaccount.jp
"""
    send_email(to_email, subject, body)
    logger.info(f"Card registration email sent to {to_email}")


def _send_invoice_email(to_email: str, full_name: str, plan: str,
                        amount_paid: int, invoice_pdf: str, invoice_url: str):
    plan_names = {"starter": "Starter", "standard": "Standard", "business": "Business"}
    plan_label = plan_names.get(plan, plan)
    amount_str = f"¥{amount_paid // 100:,}" if amount_paid else ""
    subject = f"【NextAccount】{datetime.now().strftime('%Y年%m月')}分の請求書"
    body = f"""{full_name} 様

いつも NextAccount をご利用いただきありがとうございます。

今月分の請求書が発行されました。

【プラン】{plan_label}
【金額】{amount_str}

▼ 請求書PDF をダウンロード
{invoice_pdf}

▼ 請求書をブラウザで確認
{invoice_url}

---
NextAccount サポートチーム
support@nextaccount.jp
"""
    send_email(to_email, subject, body)
    logger.info(f"Invoice email sent to {to_email}")


def _send_trial_ending_email(to_email: str, full_name: str, trial_end_dt: datetime, days_left: int):
    base_url = os.environ.get("BASE_URL", "https://nextaccount.jp")
    end_str = trial_end_dt.strftime("%Y年%m月%d日")
    data_expiry = (trial_end_dt + timedelta(days=30)).strftime("%Y年%m月%d日")

    if days_left == 1:
        subject = "【NextAccount】無料トライアルが明日終了します"
        body = f"""{full_name} 様

明日（{end_str}）をもって、NextAccount の無料トライアルが終了します。

【ご注意】
・トライアル終了後はダッシュボードへのアクセスができなくなります。
・入力済みのデータはトライアル終了後 30日間（{data_expiry}まで）保持されます。
・30日以内にプランを登録すると、全データに引き続きアクセスできます。

▼ プランを登録する（30日間無料トライアル付き）
{base_url}/dashboard.html

引き続きご利用いただける場合は、ダッシュボードからプランをお選びください。

---
NextAccount サポートチーム
support@nextaccount.jp
"""
    else:
        subject = f"【NextAccount】無料トライアルが{days_left}日後に終了します"
        body = f"""{full_name} 様

NextAccount の無料トライアルは {end_str} に終了します。

引き続きご利用いただく場合は、ダッシュボードからプランをお選びください。
プランは月額¥980（Starter）からご用意しています。

▼ プランを選択する
{base_url}/dashboard.html

【プラン一覧】
・Starter  ¥980/月  — 30枚/月
・Standard ¥1,980/月 — 100枚/月
・Business ¥5,980/月 — 枚数無制限

---
NextAccount サポートチーム
support@nextaccount.jp
"""
    send_email(to_email, subject, body)
    logger.info(f"Trial ending email ({days_left}d) sent to {to_email}")


def _check_trial_ending_tomorrow():
    """毎日実行：翌日トライアル終了の組織に1日前メールを送信"""
    try:
        from api.helpers import get_db_connection, get_db_cursor
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        now = datetime.now(timezone.utc)
        tomorrow_start = now + timedelta(days=1)
        tomorrow_end = now + timedelta(days=2)
        cur.execute(
            """SELECT o.id, o.trial_ends_at, u.email, u.full_name
               FROM organizations o
               JOIN users u ON u.organization_id = o.id AND u.is_admin = TRUE
               WHERE o.subscription_status = 'trial'
                 AND o.trial_ends_at >= %s
                 AND o.trial_ends_at < %s""",
            (tomorrow_start, tomorrow_end)
        )
        rows = cur.fetchall()
        conn.close()
        for row in rows:
            _send_trial_ending_email(
                row["email"], row["full_name"], row["trial_ends_at"], days_left=1
            )
    except Exception as e:
        logger.error(f"Trial reminder check error: {e}")


def run_daily_trial_reminder():
    """バックグラウンドで毎日09:00 JST に実行"""
    while True:
        now = datetime.now(timezone.utc)
        # 翌日の09:00 JST（00:00 UTC）まで待機
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_run - now).total_seconds()
        time.sleep(max(wait_seconds, 3600))
        _check_trial_ending_tomorrow()


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
    reminder_thread = threading.Thread(target=run_daily_trial_reminder, name="trial-reminder", daemon=True)
    reminder_thread.start()
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Flask サーバー起動: 0.0.0.0:{port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
