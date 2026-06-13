"""Setup Wizard API Routes"""
from __future__ import annotations
from flask import Blueprint, request
import logging
from .helpers import (
    get_db_connection, get_db_cursor, require_auth,
    success_response, error_response
)
from datetime import datetime, date

logger = logging.getLogger(__name__)
setup_bp = Blueprint("setup", __name__, url_prefix="/api/setup")

@setup_bp.route("/progress", methods=["GET"])
@require_auth
def get_progress():
    """セットアップ進行状況を取得"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            "SELECT step_completed, is_completed FROM setup_progress WHERE organization_id=%s",
            (org_id,)
        )
        progress = cur.fetchone()
        conn.close()

        if not progress:
            return error_response("Setup progress not found", 404)

        return success_response({
            "step_completed": progress["step_completed"],
            "is_completed": progress["is_completed"]
        })

    except Exception as e:
        logger.error(f"Get progress error: {e}")
        return error_response(str(e), 500)

@setup_bp.route("/step1", methods=["POST"])
@require_auth
def setup_step1():
    """STEP 1: 会計年度設定"""
    try:
        user_id = request.user_id
        org_id = request.organization_id
        data = request.get_json()

        start_month = data.get("start_month")
        if not start_month or not (1 <= int(start_month) <= 12):
            return error_response("Invalid start_month")

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 会計年度を計算
        today = date.today()
        current_year = today.year
        m = int(start_month)
        end_month = 12 if m == 1 else m - 1

        if m <= today.month:
            fiscal_year_start = date(current_year, m, 1)
            fiscal_year_end = date(current_year + 1 if m > 1 else current_year, end_month, 28)
        else:
            fiscal_year_start = date(current_year - 1, m, 1)
            fiscal_year_end = date(current_year, end_month, 28)

        # 既存の会計年度を削除
        cur.execute("DELETE FROM accounting_periods WHERE organization_id=%s", (org_id,))

        # 新しい会計年度を作成
        cur.execute(
            """INSERT INTO accounting_periods (organization_id, fiscal_year_start, fiscal_year_end, start_month, is_active)
               VALUES (%s, %s, %s, %s, %s)""",
            (org_id, fiscal_year_start, fiscal_year_end, start_month, True)
        )

        # 進行状況を更新
        cur.execute(
            "UPDATE setup_progress SET step_completed=1 WHERE organization_id=%s",
            (org_id,)
        )

        conn.commit()
        conn.close()

        return success_response({
            "step": 1,
            "fiscal_year_start": fiscal_year_start.isoformat(),
            "fiscal_year_end": fiscal_year_end.isoformat()
        })

    except Exception as e:
        logger.error(f"Setup step1 error: {e}")
        return error_response(str(e), 500)

@setup_bp.route("/step2", methods=["POST"])
@require_auth
def setup_step2():
    """STEP 2: 部門管理"""
    try:
        org_id = request.organization_id
        data = request.get_json()
        departments = data.get("departments", [])

        if not isinstance(departments, list):
            return error_response("departments must be a list")

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 既存部門を削除
        cur.execute("DELETE FROM departments WHERE organization_id=%s", (org_id,))

        # 新部門を追加
        for dept in departments:
            name = dept.get("name", "").strip()
            if not name:
                continue

            cur.execute(
                "INSERT INTO departments (organization_id, name, code) VALUES (%s, %s, %s)",
                (org_id, name, dept.get("code"))
            )

        # 進行状況を更新
        cur.execute(
            "UPDATE setup_progress SET step_completed=2 WHERE organization_id=%s",
            (org_id,)
        )

        conn.commit()

        # 作成した部門を返す
        cur.execute("SELECT id, name FROM departments WHERE organization_id=%s", (org_id,))
        created_depts = cur.fetchall()
        conn.close()

        return success_response({
            "step": 2,
            "departments": [{"id": d["id"], "name": d["name"]} for d in created_depts]
        })

    except Exception as e:
        logger.error(f"Setup step2 error: {e}")
        return error_response(str(e), 500)

@setup_bp.route("/step3", methods=["POST"])
@require_auth
def setup_step3():
    """STEP 3: 従業員管理"""
    try:
        org_id = request.organization_id
        data = request.get_json()
        employees = data.get("employees", [])

        if not isinstance(employees, list):
            return error_response("employees must be a list")

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 既存従業員を削除
        cur.execute("DELETE FROM employees WHERE organization_id=%s", (org_id,))

        logger.info(f"Step3: Processing {len(employees)} employees for org {org_id}")

        created_count = 0
        for emp in employees:
            email = emp.get("email", "").strip().lower()
            full_name = emp.get("full_name", "").strip()
            slack_user_id = emp.get("slack_user_id", "").strip()
            department_id = emp.get("department_id")

            logger.debug(f"Processing employee: {full_name} ({email}), dept_id={department_id}")

            if not email or not full_name:
                logger.warning(f"Skipping employee with missing email or name")
                continue

            try:
                cur.execute(
                    """INSERT INTO employees (organization_id, full_name, email, slack_user_id, department_id, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (org_id, full_name, email, slack_user_id or None, department_id, True)
                )
                created_count += 1
                logger.info(f"Successfully inserted employee: {full_name} ({email})")
            except Exception as e:
                logger.error(f"Failed to insert employee {full_name} ({email}): {e}", exc_info=True)
                continue

        logger.info(f"Step3 completed: {created_count}/{len(employees)} employees created")

        # 進行状況を更新
        cur.execute(
            "UPDATE setup_progress SET step_completed=3 WHERE organization_id=%s",
            (org_id,)
        )

        conn.commit()

        # デバッグ: 実際に保存された従業員をカウント
        cur.execute(
            "SELECT COUNT(*) as count FROM employees WHERE organization_id=%s",
            (org_id,)
        )
        db_count = cur.fetchone()["count"]
        logger.info(f"Database verification: {db_count} total employees for org {org_id}")

        # 作成した従業員を返す
        cur.execute(
            """SELECT id, full_name, email FROM employees
               WHERE organization_id=%s ORDER BY full_name""",
            (org_id,)
        )
        created_emps = cur.fetchall()
        conn.close()

        return success_response({
            "step": 3,
            "employees_created": created_count,
            "employees": [
                {"id": e["id"], "name": e["full_name"], "email": e["email"]}
                for e in created_emps
            ]
        })

    except Exception as e:
        logger.error(f"Setup step3 error: {e}")
        return error_response(str(e), 500)

@setup_bp.route("/step4", methods=["POST"])
@require_auth
def setup_step4():
    """STEP 4: Slack ワークスペース連携"""
    try:
        org_id = request.organization_id
        data = request.get_json()

        workspace_id = data.get("workspace_id", "").strip()
        workspace_name = data.get("workspace_name", "").strip()
        channel_id = data.get("channel_id", "").strip()
        channel_name = data.get("channel_name", "#経費申請").strip()
        bot_token = data.get("bot_token", "").strip()

        if not workspace_id or not workspace_name:
            return error_response("workspace_id and workspace_name required")

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 既存のSlack接続を削除
        cur.execute("DELETE FROM slack_workspaces WHERE organization_id=%s", (org_id,))

        # 新しいSlack接続を追加
        cur.execute(
            """INSERT INTO slack_workspaces
               (organization_id, workspace_id, workspace_name, bot_token, channel_id, channel_name, is_connected)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (org_id, workspace_id, workspace_name, bot_token, channel_id, channel_name, bool(bot_token))
        )

        # 進行状況を更新
        cur.execute(
            "UPDATE setup_progress SET step_completed=4, is_completed=TRUE, completed_at=CURRENT_TIMESTAMP WHERE organization_id=%s",
            (org_id,)
        )

        conn.commit()
        conn.close()

        return success_response({
            "step": 4,
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "channel_name": channel_name,
            "is_connected": bool(bot_token)
        })

    except Exception as e:
        logger.error(f"Setup step4 error: {e}")
        return error_response(str(e), 500)

@setup_bp.route("/summary", methods=["GET"])
@require_auth
def get_setup_summary():
    """セットアップサマリーを取得"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 組織情報
        cur.execute("SELECT name FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()

        # 会計年度
        cur.execute("SELECT fiscal_year_start, fiscal_year_end FROM accounting_periods WHERE organization_id=%s", (org_id,))
        period = cur.fetchone()

        # 部門数
        cur.execute("SELECT COUNT(*) as count FROM departments WHERE organization_id=%s", (org_id,))
        dept_count = cur.fetchone()["count"]

        # 従業員数
        cur.execute("SELECT COUNT(*) as count FROM employees WHERE organization_id=%s", (org_id,))
        emp_count = cur.fetchone()["count"]

        # Slack接続
        cur.execute("SELECT workspace_name, is_connected FROM slack_workspaces WHERE organization_id=%s", (org_id,))
        slack = cur.fetchone()

        conn.close()

        return success_response({
            "organization_name": org["name"] if org else None,
            "fiscal_year_start": period["fiscal_year_start"].isoformat() if period else None,
            "fiscal_year_end": period["fiscal_year_end"].isoformat() if period else None,
            "department_count": dept_count,
            "employee_count": emp_count,
            "slack_workspace": slack["workspace_name"] if slack else None,
            "slack_connected": slack["is_connected"] if slack else False
        })

    except Exception as e:
        logger.error(f"Get setup summary error: {e}")
        return error_response(str(e), 500)
