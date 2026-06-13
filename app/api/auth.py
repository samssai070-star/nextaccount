"""Authentication API Routes"""
from __future__ import annotations
from flask import Blueprint, request
from datetime import datetime, timezone
import logging
from .helpers import (
    get_db_connection, get_db_cursor, hash_password, generate_token,
    require_auth, success_response, error_response
)

logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@auth_bp.route("/register", methods=["POST"])
def register():
    """ユーザー登録"""
    try:
        data = request.get_json()
        org_name = data.get("organization_name", "").strip()
        user_name = data.get("user_name", "").strip()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "").strip()

        # バリデーション
        if not all([org_name, user_name, email, password]):
            return error_response("Missing required fields")
        if len(password) < 8:
            return error_response("Password must be at least 8 characters")
        if "@" not in email:
            return error_response("Invalid email format")

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # チェック: メール重複
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return error_response("Email already registered", 409)

        # 組織を作成
        cur.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (org_name,)
        )
        org_id = cur.fetchone()["id"]

        # ユーザーを作成
        password_hash = hash_password(password)
        cur.execute(
            """INSERT INTO users (organization_id, email, password_hash, full_name, is_admin, role, client_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (org_id, email, password_hash, user_name, True, 'admin', None)
        )
        user_id = cur.fetchone()["id"]

        # 初期化進行テーブルに追加
        cur.execute(
            "INSERT INTO setup_progress (organization_id, user_id, step_completed) VALUES (%s, %s, %s)",
            (org_id, user_id, 0)
        )

        conn.commit()
        conn.close()

        # トークン生成
        token = generate_token(user_id, org_id)

        return success_response({
            "user_id": user_id,
            "organization_id": org_id,
            "email": email,
            "token": token
        }, 201)

    except Exception as e:
        logger.error(f"Registration error: {e}")
        return error_response(str(e), 500)

@auth_bp.route("/login", methods=["POST"])
def login():
    """ログイン"""
    try:
        data = request.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "").strip()

        if not email or not password:
            return error_response("Email and password required")

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # ユーザー取得
        cur.execute(
            "SELECT id, organization_id, password_hash, is_active FROM users WHERE email=%s",
            (email,)
        )
        user = cur.fetchone()

        if not user:
            conn.close()
            return error_response("Invalid email or password", 401)

        if not user["is_active"]:
            conn.close()
            return error_response("User account is disabled", 403)

        # パスワード検証
        if hash_password(password) != user["password_hash"]:
            conn.close()
            return error_response("Invalid email or password", 401)

        # ログイン時刻を更新
        cur.execute(
            "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=%s",
            (user["id"],)
        )
        conn.commit()

        # ユーザーが属する organization/client を取得
        cur.execute(
            """SELECT id, name, org_type, subscription_status,
                      cancellation_effective_at, suspension_ends_at, data_deleted_at
               FROM organizations WHERE id=%s""",
            (user["organization_id"],)
        )
        org_data = cur.fetchone()

        # 即時解約済み（トライアルキャンセル）はログイン不可
        if org_data and org_data["subscription_status"] == "canceled" and org_data["data_deleted_at"]:
            conn.close()
            return error_response("このアカウントは解約済みです", 403)

        # canceling → suspended 自動遷移
        if org_data and org_data["subscription_status"] == "canceling":
            eff = org_data["cancellation_effective_at"]
            if eff:
                if eff.tzinfo is None:
                    eff = eff.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= eff:
                    cur.execute(
                        "UPDATE organizations SET subscription_status='suspended', updated_at=NOW() WHERE id=%s",
                        (org_data["id"],)
                    )
                    conn.commit()
                    org_data = dict(org_data)
                    org_data["subscription_status"] = "suspended"

        # ユーザーが属しているクライアントを取得
        cur.execute(
            """SELECT id, name FROM clients WHERE org_id=%s AND id IN (
                   SELECT client_id FROM users WHERE id=%s AND client_id IS NOT NULL
               )""",
            (user["organization_id"], user["id"])
        )
        user_clients = cur.fetchall() or []

        conn.close()

        # トークンは未生成。select エンドポイントで生成する
        return success_response({
            "user_id": user["id"],
            "email": email,
            "organization": {
                "id": org_data["id"],
                "name": org_data["name"],
                "org_type": org_data.get("org_type") or "company",
                "subscription_status": org_data.get("subscription_status") or "trial",
                "cancellation_effective_at": org_data["cancellation_effective_at"].isoformat() if org_data.get("cancellation_effective_at") else None,
                "suspension_ends_at": org_data["suspension_ends_at"].isoformat() if org_data.get("suspension_ends_at") else None
            },
            "user_clients": [{"id": str(c["id"]), "name": c["name"]} for c in user_clients]
        })

    except Exception as e:
        logger.error(f"Login error: {e}")
        return error_response(str(e), 500)

@auth_bp.route("/select", methods=["POST"])
def select_organization():
    """organization/client を選択してトークンを取得"""
    try:
        data = request.get_json() or {}
        user_id = data.get("user_id")
        org_id = data.get("organization_id")
        client_id = data.get("client_id")  # Optional

        if not user_id or not org_id:
            return error_response("user_id and organization_id required", 400)

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # ユーザー確認
        cur.execute(
            "SELECT id, organization_id FROM users WHERE id=%s AND organization_id=%s",
            (user_id, org_id)
        )
        user = cur.fetchone()
        if not user:
            conn.close()
            return error_response("Invalid user or organization", 401)

        # client_id が指定されている場合、ユーザーがそのclientに属するか確認
        if client_id:
            cur.execute(
                """SELECT id FROM clients
                   WHERE id=%s AND org_id=%s AND id IN (
                       SELECT client_id FROM users WHERE id=%s
                   )""",
                (client_id, org_id, user_id)
            )
            if not cur.fetchone():
                conn.close()
                return error_response("User does not belong to this client", 403)

        conn.close()

        # トークン生成（client_id は後で別途保存）
        token = generate_token(user_id, org_id)

        return success_response({
            "token": token,
            "user_id": user_id,
            "organization_id": org_id,
            "client_id": client_id
        })

    except Exception as e:
        logger.error(f"Select organization error: {e}")
        return error_response(str(e), 500)


@auth_bp.route("/me", methods=["GET"])
@require_auth
def get_current_user():
    """現在のユーザー情報を取得"""
    try:
        user_id = request.user_id
        org_id = request.organization_id

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # ユーザー情報取得
        cur.execute(
            """SELECT u.id, u.email, u.full_name, u.is_admin, u.role, u.client_id,
                      o.name as organization_name, o.org_type
               FROM users u
               JOIN organizations o ON u.organization_id = o.id
               WHERE u.id=%s AND u.organization_id=%s""",
            (user_id, org_id)
        )
        user = cur.fetchone()
        conn.close()

        if not user:
            return error_response("User not found", 404)

        return success_response({
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "organization_id": org_id,
            "organization_name": user["organization_name"],
            "is_admin": user["is_admin"],
            "role": user.get("role") or "staff",
            "client_id": user.get("client_id"),
            "org_type": user.get("org_type") or "company",
        })

    except Exception as e:
        logger.error(f"Get user error: {e}")
        return error_response(str(e), 500)

@auth_bp.route("/logout", methods=["POST"])
@require_auth
def logout():
    """ログアウト"""
    return success_response({"message": "Logged out successfully"})
