"""Organization API — 組織管理"""
from __future__ import annotations
import os, secrets, logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request
from .helpers import get_db_connection, get_db_cursor, require_auth, success_response, error_response

logger = logging.getLogger(__name__)
org_bp = Blueprint("org", __name__, url_prefix="/api/org")

ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY", "")


def _require_admin():
    """管理者キーによる認証"""
    key = request.headers.get("X-Admin-Key", "")
    if not ADMIN_SECRET_KEY or key != ADMIN_SECRET_KEY:
        return error_response("Unauthorized", 401)
    return None


@org_bp.route("/admin/issue-upgrade-code", methods=["POST"])
def issue_upgrade_code():
    """【管理者専用】授権コードを発行して顧客メールに送信"""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    try:
        data = request.get_json() or {}
        org_email = (data.get("organization_email") or "").strip().lower()
        if not org_email:
            return error_response("organization_email required", 400)

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 対象組織を確認（メールは users テーブルに格納）
        cur.execute(
            """SELECT o.id, o.name, u.email, o.org_type
               FROM organizations o
               JOIN users u ON u.organization_id = o.id
               WHERE u.email=%s AND u.is_admin=TRUE
               LIMIT 1""",
            (org_email,)
        )
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)
        if org["org_type"] == "firm":
            conn.close()
            return error_response("この組織は既に会計事務所です", 409)

        # 未使用コードが既にあれば無効化
        cur.execute(
            "UPDATE org_upgrade_codes SET is_used=TRUE WHERE organization_id=%s AND is_used=FALSE",
            (org["id"],)
        )

        # 新しいコードを生成（8文字の大文字英数字）
        code = secrets.token_hex(4).upper()
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        cur.execute(
            """INSERT INTO org_upgrade_codes (organization_id, code, expires_at)
               VALUES (%s, %s, %s)""",
            (org["id"], code, expires_at)
        )
        conn.commit()
        conn.close()

        # メール送信
        _send_upgrade_code_email(org["email"], org["name"], code, expires_at)

        logger.info(f"Upgrade code issued for org {org['id']} ({org_email})")
        return success_response({
            "organization": org["name"],
            "email": org["email"],
            "code": code,
            "expires_at": expires_at.isoformat()
        })

    except Exception as e:
        logger.error(f"issue_upgrade_code error: {e}")
        return error_response(str(e), 500)


def _send_upgrade_code_email(to_email, org_name, code, expires_at):
    """授権コードをメールで送信"""
    try:
        import sys
        sys.path.insert(0, '/app')
        from main import send_email
        expires_str = expires_at.strftime("%Y年%m月%d日")
        subject = "【NextAccount】会計事務所プランへの変更 授権コードのご案内"
        body = f"""{org_name} ご担当者様

NextAccount をご利用いただきありがとうございます。

会計事務所プランへの変更申請を承認しました。
以下の授権コードをダッシュボードにご入力ください。

━━━━━━━━━━━━━━━━━━━
授権コード：{code}
有効期限：{expires_str}
━━━━━━━━━━━━━━━━━━━

【変更手順】
1. NextAccount ダッシュボードにログイン
2. 「ユーザーアカウント」セクションで「会計事務所に変更」をクリック
3. 上記の授権コードを入力して「変更を確定」

このコードは1回のみ有効です。有効期限を過ぎた場合は再度お問い合わせください。

ご不明な点は support@nextaccount.jp までお問い合わせください。

NextAccount サポートチーム
"""
        send_email(to_email, subject, body)
    except Exception as e:
        logger.error(f"Failed to send upgrade code email: {e}")


@org_bp.route("/admin/organizations", methods=["GET"])
def list_organizations():
    """【管理者専用】全組織一覧を取得"""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute("""
            SELECT
                o.id, o.name, o.org_type,
                o.subscription_status, o.plan,
                o.trial_ends_at, o.created_at,
                u.email AS admin_email,
                COALESCE(emp.cnt, 0) AS employee_count,
                sp.is_completed AS setup_completed,
                sp.step_completed,
                sw.is_connected AS slack_connected,
                sw.workspace_name
            FROM organizations o
            LEFT JOIN users u ON u.organization_id = o.id AND u.is_admin = TRUE
            LEFT JOIN (
                SELECT organization_id, COUNT(*) AS cnt
                FROM employees WHERE is_active = TRUE
                GROUP BY organization_id
            ) emp ON emp.organization_id = o.id
            LEFT JOIN setup_progress sp ON sp.organization_id = o.id
            LEFT JOIN slack_workspaces sw ON sw.organization_id = o.id
            ORDER BY o.created_at DESC
        """)
        orgs = cur.fetchall()
        conn.close()

        return success_response({
            "organizations": [{
                "id": org["id"],
                "name": org["name"],
                "admin_email": org["admin_email"] or "",
                "org_type": org["org_type"] or "company",
                "subscription_status": org["subscription_status"] or "trial",
                "plan": org["plan"] or "",
                "trial_ends_at": org["trial_ends_at"].isoformat() if org["trial_ends_at"] else None,
                "created_at": org["created_at"].isoformat() if org["created_at"] else None,
                "employee_count": int(org["employee_count"]),
                "setup_completed": bool(org["setup_completed"]),
                "step_completed": org["step_completed"] or 0,
                "slack_connected": bool(org["slack_connected"]),
                "workspace_name": org["workspace_name"] or ""
            } for org in orgs]
        })

    except Exception as e:
        logger.error(f"list_organizations error: {e}")
        return error_response(str(e), 500)


@org_bp.route("/admin/organization/<int:org_id>", methods=["GET"])
def get_organization_detail(org_id):
    """【管理者専用】組織の詳細情報を取得"""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 組織基本情報
        cur.execute("""
            SELECT
                o.id, o.name, o.org_type, o.subscription_status, o.plan,
                o.trial_ends_at, o.created_at, o.stripe_customer_id,
                u.email AS admin_email, u.full_name AS admin_name,
                COALESCE(emp.cnt, 0) AS employee_count,
                sp.is_completed AS setup_completed, sp.step_completed,
                sw.workspace_name, sw.is_connected AS slack_connected
            FROM organizations o
            LEFT JOIN users u ON u.organization_id = o.id AND u.is_admin = TRUE
            LEFT JOIN (
                SELECT organization_id, COUNT(*) AS cnt
                FROM employees WHERE is_active = TRUE
                GROUP BY organization_id
            ) emp ON emp.organization_id = o.id
            LEFT JOIN setup_progress sp ON sp.organization_id = o.id
            LEFT JOIN slack_workspaces sw ON sw.organization_id = o.id
            WHERE o.id = %s
        """, (org_id,))
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)

        # 月別発票アップロード統計（過去12ヶ月）
        # company の場合：自社従業員の仕訳
        # firm の場合：顧問先（clients）の仕訳
        if org["org_type"] == "company":
            cur.execute("""
                SELECT
                    DATE_TRUNC('month', ae.event_date)::date AS month,
                    COUNT(*) AS count
                FROM accounting_events ae
                WHERE ae.employee_name IN (
                    SELECT full_name FROM employees WHERE organization_id = %s
                )
                  AND ae.event_date >= CURRENT_DATE - INTERVAL '12 months'
                GROUP BY DATE_TRUNC('month', ae.event_date)
                ORDER BY month DESC
            """, (org_id,))
        else:  # firm
            cur.execute("""
                SELECT
                    DATE_TRUNC('month', ae.event_date)::date AS month,
                    COUNT(*) AS count
                FROM accounting_events ae
                WHERE ae.client_id IN (
                    SELECT id FROM clients WHERE org_id = %s
                )
                  AND ae.event_date >= CURRENT_DATE - INTERVAL '12 months'
                GROUP BY DATE_TRUNC('month', ae.event_date)
                ORDER BY month DESC
            """, (org_id,))

        monthly_stats = []
        for row in cur.fetchall():
            if row["month"]:
                monthly_stats.append({
                    "month": row["month"].isoformat(),
                    "count": int(row["count"])
                })

        # 総発票アップロード数
        if org["org_type"] == "company":
            cur.execute("""
                SELECT COUNT(*) AS total FROM accounting_events
                WHERE employee_name IN (
                    SELECT full_name FROM employees WHERE organization_id = %s
                )
            """, (org_id,))
        else:
            cur.execute("""
                SELECT COUNT(*) AS total FROM accounting_events
                WHERE client_id IN (
                    SELECT id FROM clients WHERE org_id = %s
                )
            """, (org_id,))
        total_events = cur.fetchone()["total"]

        # 顧問先リスト（firm の場合のみ）
        clients = []
        if org["org_type"] == "firm":
            cur.execute("""
                SELECT id, name
                FROM clients
                WHERE org_id = %s
                ORDER BY name
            """, (org_id,))
            for row in cur.fetchall():
                clients.append({
                    "id": row["id"],
                    "name": row["name"]
                })

        conn.close()

        return success_response({
            "organization": {
                "id": org["id"],
                "name": org["name"],
                "admin_email": org["admin_email"] or "",
                "admin_name": org["admin_name"] or "",
                "org_type": org["org_type"] or "company",
                "subscription_status": org["subscription_status"] or "trial",
                "plan": org["plan"] or "",
                "trial_ends_at": org["trial_ends_at"].isoformat() if org["trial_ends_at"] else None,
                "created_at": org["created_at"].isoformat() if org["created_at"] else None,
                "stripe_customer_id": org["stripe_customer_id"] or "",
                "employee_count": int(org["employee_count"]),
                "setup_completed": bool(org["setup_completed"]),
                "step_completed": org["step_completed"] or 0,
                "slack_workspace": org["workspace_name"] or "",
                "slack_connected": bool(org["slack_connected"]),
                "total_events": int(total_events),
                "monthly_stats": monthly_stats,
                "clients": clients
            }
        })

    except Exception as e:
        logger.error(f"get_organization_detail error: {e}")
        return error_response(str(e), 500)


@org_bp.route("/admin/stats", methods=["GET"])
def get_admin_stats():
    """【管理者専用】統計情報を取得"""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute("SELECT COUNT(*) AS total FROM organizations")
        total = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS cnt FROM organizations WHERE org_type='firm'")
        firms = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM organizations WHERE subscription_status='trial'")
        trials = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM organizations WHERE subscription_status='active'")
        active = cur.fetchone()["cnt"]

        conn.close()

        return success_response({
            "total": int(total),
            "firms": int(firms),
            "companies": int(total) - int(firms),
            "trials": int(trials),
            "active": int(active)
        })

    except Exception as e:
        logger.error(f"get_admin_stats error: {e}")
        return error_response(str(e), 500)


@org_bp.route("/request-cancellation", methods=["POST"])
@require_auth
def request_cancellation():
    """解約を申請（次次月から正式解約・3ヶ月間ログインのみ可能）"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            "SELECT subscription_status FROM organizations WHERE id=%s",
            (org_id,)
        )
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)

        if org["subscription_status"] in ("canceling", "suspended"):
            conn.close()
            return error_response("既に解約予定または解約済みです", 409)

        now = datetime.now(timezone.utc)
        # 次次月 1日（今月・来月は課金継続）
        month = now.month + 2
        year = now.year
        if month > 12:
            month -= 12
            year += 1
        cancellation_effective_at = datetime(year, month, 1, tzinfo=timezone.utc)
        # サービス停止から3ヶ月間はログイン・再契約可能
        suspension_ends_at = cancellation_effective_at + timedelta(days=92)

        cur.execute(
            """UPDATE organizations
               SET subscription_status='canceling',
                   cancellation_effective_at=%s,
                   suspension_ends_at=%s,
                   updated_at=NOW()
               WHERE id=%s""",
            (cancellation_effective_at, suspension_ends_at, org_id)
        )
        conn.commit()
        conn.close()

        logger.info(f"Cancellation requested for org {org_id}, effective: {cancellation_effective_at}")
        return success_response({
            "status": "canceling",
            "cancellation_effective_at": cancellation_effective_at.isoformat(),
            "suspension_ends_at": suspension_ends_at.isoformat(),
            "message": "解約申請が完了しました。"
        })

    except Exception as e:
        logger.error(f"request_cancellation error: {e}")
        return error_response(str(e), 500)


@org_bp.route("/cancel-trial", methods=["POST"])
@require_auth
def cancel_trial():
    """トライアル中の即時解約（データ削除・ログイン不可）"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute("SELECT subscription_status FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)

        if org["subscription_status"] != "trial":
            conn.close()
            return error_response("トライアル中のみ利用できます", 409)

        cur.execute(
            """UPDATE organizations
               SET subscription_status='canceled',
                   data_deleted_at=NOW(),
                   updated_at=NOW()
               WHERE id=%s""",
            (org_id,)
        )
        conn.commit()
        conn.close()

        logger.info(f"Trial canceled for org {org_id}")
        return success_response({"status": "canceled", "message": "解約が完了しました。"})

    except Exception as e:
        logger.error(f"cancel_trial error: {e}")
        return error_response(str(e), 500)


@org_bp.route("/cancel-cancellation", methods=["POST"])
@require_auth
def cancel_cancellation():
    """解約申請を取り消してアクティブに戻す"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute("SELECT subscription_status FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)

        if org["subscription_status"] != "canceling":
            conn.close()
            return error_response("解約手続き中ではありません", 409)

        cur.execute(
            """UPDATE organizations
               SET subscription_status='active',
                   cancellation_effective_at=NULL,
                   suspension_ends_at=NULL,
                   updated_at=NOW()
               WHERE id=%s""",
            (org_id,)
        )
        conn.commit()
        conn.close()

        logger.info(f"Cancellation withdrawn for org {org_id}")
        return success_response({"status": "active", "message": "解約のお申し込みを取り消しました。"})

    except Exception as e:
        logger.error(f"cancel_cancellation error: {e}")
        return error_response(str(e), 500)


@org_bp.route("/resubscribe", methods=["POST"])
@require_auth
def resubscribe():
    """解約後の再契約（試用期間なし）"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """SELECT subscription_status, suspension_ends_at
               FROM organizations WHERE id=%s""",
            (org_id,)
        )
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)

        if org["subscription_status"] not in ("canceling", "suspended"):
            conn.close()
            return error_response("再契約対象外のステータスです", 409)

        # 3ヶ月の停止期限を過ぎていたら再契約不可
        if org["suspension_ends_at"]:
            now = datetime.now(timezone.utc)
            ends = org["suspension_ends_at"]
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            if now > ends:
                conn.close()
                return error_response("再契約可能期間が終了しました", 403)

        cur.execute(
            """UPDATE organizations
               SET subscription_status='active',
                   cancellation_effective_at=NULL,
                   suspension_ends_at=NULL,
                   trial_ends_at=NULL,
                   updated_at=NOW()
               WHERE id=%s""",
            (org_id,)
        )
        conn.commit()
        conn.close()

        logger.info(f"Resubscribed org {org_id}")
        return success_response({"status": "active", "message": "再契約が完了しました。"})

    except Exception as e:
        logger.error(f"resubscribe error: {e}")
        return error_response(str(e), 500)


@org_bp.route("/update-type", methods=["PATCH"])
@require_auth
def update_org_type():
    """組織タイプを更新（company → firm）授権コード必須"""
    try:
        org_id = request.organization_id
        data = request.get_json() or {}
        org_type = (data.get("org_type") or "").strip().lower()
        auth_code = (data.get("auth_code") or "").strip()

        if org_type not in ("company", "firm"):
            return error_response("org_type は 'company' または 'firm' である必要があります", 400)

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 既に firm になっている場合
        cur.execute("SELECT org_type, name FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)
        if org["org_type"] == "firm" and org_type == "firm":
            conn.close()
            return success_response({"org_type": "firm", "message": "既に会計事務所です"})

        # 会計事務所への変更は授権コード DB 照合
        if org_type == "firm":
            if not auth_code:
                conn.close()
                return error_response("授権コードが必要です", 400)

            now = datetime.now(timezone.utc)
            cur.execute(
                """SELECT id FROM org_upgrade_codes
                   WHERE organization_id=%s AND code=%s
                     AND is_used=FALSE AND expires_at > %s""",
                (org_id, auth_code, now)
            )
            code_row = cur.fetchone()
            if not code_row:
                conn.close()
                return error_response("授権コードが無効または期限切れです", 403)

            # コードを使用済みにする
            cur.execute(
                "UPDATE org_upgrade_codes SET is_used=TRUE, used_at=NOW() WHERE id=%s",
                (code_row["id"],)
            )

        cur.execute(
            "UPDATE organizations SET org_type=%s, updated_at=NOW() WHERE id=%s",
            (org_type, org_id)
        )
        conn.commit()
        conn.close()
        logger.info(f"Org type updated: {org_id} → {org_type}")
        return success_response({"org_type": org_type})

    except Exception as e:
        logger.error(f"update_org_type error: {e}")
        return error_response(str(e), 500)
