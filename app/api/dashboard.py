"""Dashboard API Routes"""
from __future__ import annotations
from flask import Blueprint, request
import logging
from .helpers import (
    get_db_connection, get_db_cursor, require_auth,
    success_response, error_response
)

logger = logging.getLogger(__name__)
dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")

@dashboard_bp.route("/summary", methods=["GET"])
@require_auth
def get_summary():
    """ダッシュボード統計情報を取得"""
    try:
        org_id = request.organization_id
        logger.debug(f"Getting summary for org_id={org_id}")

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 組織情報
        cur.execute("SELECT name FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()

        # 会計年度
        cur.execute(
            "SELECT fiscal_year_start, fiscal_year_end FROM accounting_periods WHERE organization_id=%s",
            (org_id,)
        )
        period = cur.fetchone()

        # 従業員数（デバッグ用に両方カウント）
        cur.execute(
            "SELECT COUNT(*) as count FROM employees WHERE organization_id=%s",
            (org_id,)
        )
        total_employees = cur.fetchone()["count"]

        cur.execute(
            "SELECT COUNT(*) as count FROM employees WHERE organization_id=%s AND is_active=true",
            (org_id,)
        )
        employee_count = cur.fetchone()["count"]

        logger.info(f"Summary for org {org_id}: total_emp={total_employees}, active_emp={employee_count}")

        # 部門数
        cur.execute(
            "SELECT COUNT(*) as count FROM departments WHERE organization_id=%s AND is_active=true",
            (org_id,)
        )
        department_count = cur.fetchone()["count"]

        conn.close()

        return success_response({
            "organization_name": org["name"] if org else None,
            "fiscal_year_start": period["fiscal_year_start"].isoformat() if period else None,
            "fiscal_year_end": period["fiscal_year_end"].isoformat() if period else None,
            "employee_count": employee_count,
            "department_count": department_count
        })

    except Exception as e:
        logger.error(f"Get summary error: {e}")
        return error_response(str(e)), 500

@dashboard_bp.route("/organization", methods=["GET"])
@require_auth
def get_organization():
    """組織情報を取得"""
    try:
        org_id = request.organization_id

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            "SELECT id, name, created_at FROM organizations WHERE id=%s",
            (org_id,)
        )
        org = cur.fetchone()
        conn.close()

        if not org:
            return error_response("Organization not found"), 404

        return success_response({
            "id": org["id"],
            "name": org["name"],
            "created_at": org["created_at"].isoformat()
        })

    except Exception as e:
        logger.error(f"Get organization error: {e}")
        return error_response(str(e)), 500

@dashboard_bp.route("/departments", methods=["GET"])
@require_auth
def get_departments():
    """部門一覧を取得"""
    try:
        org_id = request.organization_id

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            "SELECT id, name, code FROM departments WHERE organization_id=%s AND is_active=true ORDER BY name",
            (org_id,)
        )
        departments = cur.fetchall()
        conn.close()

        return success_response([
            {"id": d["id"], "name": d["name"], "code": d["code"]}
            for d in departments
        ])

    except Exception as e:
        logger.error(f"Get departments error: {e}")
        return error_response(str(e)), 500

@dashboard_bp.route("/employees", methods=["GET"])
@require_auth
def get_employees():
    """従業員一覧を取得"""
    try:
        org_id = request.organization_id

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """SELECT e.id, e.full_name, e.email, e.slack_user_id, d.name as department_name
               FROM employees e
               LEFT JOIN departments d ON e.department_id = d.id
               WHERE e.organization_id=%s AND e.is_active=true
               ORDER BY e.full_name""",
            (org_id,)
        )
        employees = cur.fetchall()
        conn.close()

        return success_response([
            {
                "id": e["id"],
                "full_name": e["full_name"],
                "email": e["email"],
                "slack_user_id": e["slack_user_id"],
                "department_name": e["department_name"]
            }
            for e in employees
        ])

    except Exception as e:
        logger.error(f"Get employees error: {e}")
        return error_response(str(e)), 500

@dashboard_bp.route("/users", methods=["GET"])
@require_auth
def get_users():
    """ユーザー一覧を取得"""
    try:
        org_id = request.organization_id

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """SELECT id, email, full_name, is_admin, is_active, last_login_at
               FROM users
               WHERE organization_id=%s
               ORDER BY full_name""",
            (org_id,)
        )
        users = cur.fetchall()
        conn.close()

        return success_response([
            {
                "id": u["id"],
                "email": u["email"],
                "full_name": u["full_name"],
                "is_admin": u["is_admin"],
                "is_active": u["is_active"],
                "last_login_at": u["last_login_at"].isoformat() if u["last_login_at"] else None
            }
            for u in users
        ])

    except Exception as e:
        logger.error(f"Get users error: {e}")
        return error_response(str(e)), 500
