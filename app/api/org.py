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
