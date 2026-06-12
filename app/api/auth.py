"""Authentication API Routes"""
from __future__ import annotations
from flask import Blueprint, request
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
            return error_response("Email already registered"), 409

        # 組織を作成
        cur.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (org_name,)
        )
        org_id = cur.fetchone()["id"]

        # ユーザーを作成
        password_hash = hash_password(password)
        cur.execute(
            """INSERT INTO users (organization_id, email, password_hash, full_name, is_admin)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (org_id, email, password_hash, user_name, True)
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
        return error_response(str(e)), 500

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
            return error_response("Invalid email or password"), 401

        if not user["is_active"]:
            conn.close()
            return error_response("User account is disabled"), 403

        # パスワード検証
        if hash_password(password) != user["password_hash"]:
            conn.close()
            return error_response("Invalid email or password"), 401

        # ログイン時刻を更新
        cur.execute(
            "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=%s",
            (user["id"],)
        )
        conn.commit()
        conn.close()

        # トークン生成
        token = generate_token(user["id"], user["organization_id"])

        return success_response({
            "user_id": user["id"],
            "organization_id": user["organization_id"],
            "email": email,
            "token": token
        })

    except Exception as e:
        logger.error(f"Login error: {e}")
        return error_response(str(e)), 500

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
            """SELECT u.id, u.email, u.full_name, u.is_admin,
                      o.name as organization_name, o.org_type
               FROM users u
               JOIN organizations o ON u.organization_id = o.id
               WHERE u.id=%s AND u.organization_id=%s""",
            (user_id, org_id)
        )
        user = cur.fetchone()
        conn.close()

        if not user:
            return error_response("User not found"), 404

        return success_response({
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "organization_id": org_id,
            "organization_name": user["organization_name"],
            "is_admin": user["is_admin"],
            "org_type": user.get("org_type") or "company",
        })

    except Exception as e:
        logger.error(f"Get user error: {e}")
        return error_response(str(e)), 500

@auth_bp.route("/logout", methods=["POST"])
@require_auth
def logout():
    """ログアウト"""
    return success_response({"message": "Logged out successfully"})
