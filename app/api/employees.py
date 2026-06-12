"""Employees API — 従業員管理（client 別）"""
from __future__ import annotations
import logging
import json
from flask import Blueprint, request
from .helpers import get_db_connection, get_db_cursor, require_auth, success_response, error_response

logger = logging.getLogger(__name__)
employees_bp = Blueprint("employees", __name__, url_prefix="/api/employees")


@employees_bp.route("", methods=["GET"])
@require_auth
def list_employees():
    """従業員一覧（client 配下のみ）"""
    try:
        org_id = request.organization_id
        client_id = request.args.get("client_id")

        if not client_id:
            return error_response("client_id parameter required", 400)

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # client が user の org に属するか確認
        cur.execute(
            "SELECT id FROM clients WHERE id=%s AND org_id=%s",
            (client_id, org_id)
        )
        if not cur.fetchone():
            conn.close()
            return error_response("Client not found", 404)

        # client 配下の従業員を取得
        cur.execute(
            """SELECT id, email, full_name, role, is_active, created_at,
                      department, slack_user_id, permissions
               FROM users
               WHERE client_id=%s AND organization_id=%s
               ORDER BY full_name""",
            (client_id, org_id)
        )
        rows = cur.fetchall()
        conn.close()

        import json
        employees = [
            {
                "id": r["id"],
                "email": r["email"],
                "full_name": r["full_name"],
                "role": r.get("role") or "staff",
                "is_active": r.get("is_active", True),
                "department": r.get("department") or "",
                "slack_user_id": r.get("slack_user_id") or "",
                "permissions": json.loads(r.get("permissions") or "[]"),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
        return success_response(employees)

    except Exception as e:
        logger.error(f"list_employees error: {e}")
        return error_response(str(e), 500)


@employees_bp.route("", methods=["POST"])
@require_auth
def create_employee():
    """新規従業員登録（client に割り当て）"""
    try:
        org_id = request.organization_id
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        full_name = (data.get("full_name") or "").strip()
        client_id = data.get("client_id")
        role = (data.get("role") or "staff").strip()
        slack_user_id = (data.get("slack_user_id") or "").strip()
        permissions = data.get("permissions") or []

        if not email or not full_name or not client_id or not slack_user_id:
            return error_response("email, full_name, client_id, slack_user_id required", 400)

        if role not in ("admin", "accountant", "staff"):
            return error_response("role must be 'admin', 'accountant', or 'staff'", 400)

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # client が user の org に属するか確認
        cur.execute(
            "SELECT id FROM clients WHERE id=%s AND org_id=%s",
            (client_id, org_id)
        )
        if not cur.fetchone():
            conn.close()
            return error_response("Client not found", 404)

        # メール重複チェック
        cur.execute(
            "SELECT id FROM users WHERE email=%s",
            (email,)
        )
        if cur.fetchone():
            conn.close()
            return error_response("Email already registered", 409)

        # 従業員を作成（password_hash は仮置き）
        from .helpers import hash_password
        password_hash = hash_password("temp_password_123")  # 仮パスワード
        department = (data.get("department") or "").strip()
        permissions_json = json.dumps(permissions)

        cur.execute(
            """INSERT INTO users
               (organization_id, client_id, email, full_name, password_hash, role, is_active,
                department, slack_user_id, permissions)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, email, full_name, role, is_active, created_at, department, slack_user_id, permissions""",
            (org_id, client_id, email, full_name, password_hash, role, True,
             department, slack_user_id, permissions_json)
        )
        row = cur.fetchone()
        conn.commit()
        conn.close()

        return success_response({
            "id": row["id"],
            "email": row["email"],
            "full_name": row["full_name"],
            "role": row["role"],
            "is_active": row["is_active"],
            "department": row.get("department") or "",
            "slack_user_id": row.get("slack_user_id") or "",
            "permissions": json.loads(row.get("permissions") or "[]"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }, 201)

    except Exception as e:
        logger.error(f"create_employee error: {e}")
        return error_response(str(e), 500)


@employees_bp.route("/<user_id>", methods=["PATCH"])
@require_auth
def update_employee(user_id):
    """従業員情報を更新"""
    try:
        org_id = request.organization_id
        data = request.get_json() or {}
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # ユーザーが org に属するか確認
        cur.execute(
            "SELECT id, client_id FROM users WHERE id=%s AND organization_id=%s",
            (user_id, org_id)
        )
        user = cur.fetchone()
        if not user:
            conn.close()
            return error_response("User not found", 404)

        updates = []
        values = []

        if "full_name" in data:
            updates.append("full_name=%s")
            values.append(data["full_name"].strip())

        if "role" in data:
            role = data["role"].strip()
            if role not in ("admin", "accountant", "staff"):
                conn.close()
                return error_response("role must be 'admin', 'accountant', or 'staff'", 400)
            updates.append("role=%s")
            values.append(role)

        if "is_active" in data:
            updates.append("is_active=%s")
            values.append(bool(data["is_active"]))

        if "department" in data:
            updates.append("department=%s")
            values.append((data["department"] or "").strip())

        if "slack_user_id" in data:
            slack_id = (data["slack_user_id"] or "").strip()
            if not slack_id:
                conn.close()
                return error_response("slack_user_id cannot be empty", 400)
            updates.append("slack_user_id=%s")
            values.append(slack_id)

        if "permissions" in data:
            updates.append("permissions=%s")
            values.append(json.dumps(data.get("permissions") or []))

        if not updates:
            conn.close()
            return error_response("No fields to update", 400)

        updates.append("updated_at=NOW()")
        values.append(user_id)
        values.append(org_id)

        cur.execute(
            f"UPDATE users SET {', '.join(updates)} WHERE id=%s AND organization_id=%s",
            values
        )
        conn.commit()
        conn.close()
        logger.info(f"Employee updated: {user_id}")
        return success_response({"updated": True})

    except Exception as e:
        logger.error(f"update_employee error: {e}")
        return error_response(str(e), 500)


@employees_bp.route("/<user_id>", methods=["DELETE"])
@require_auth
def delete_employee(user_id):
    """従業員を無効化（ソフトデリート）"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # ユーザーが org に属するか確認
        cur.execute(
            "SELECT id FROM users WHERE id=%s AND organization_id=%s",
            (user_id, org_id)
        )
        if not cur.fetchone():
            conn.close()
            return error_response("User not found", 404)

        # 無効化（削除ではなく is_active を FALSE に）
        cur.execute(
            "UPDATE users SET is_active=FALSE, updated_at=NOW() WHERE id=%s AND organization_id=%s",
            (user_id, org_id)
        )
        conn.commit()
        conn.close()
        logger.info(f"Employee deactivated: {user_id}")
        return success_response({"deleted": True})

    except Exception as e:
        logger.error(f"delete_employee error: {e}")
        return error_response(str(e), 500)
