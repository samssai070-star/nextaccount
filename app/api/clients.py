"""Clients API — 会計事務所の顧問先管理"""
from __future__ import annotations
import logging
from flask import Blueprint, request
from .helpers import get_db_connection, get_db_cursor, require_auth, success_response, error_response

logger = logging.getLogger(__name__)
clients_bp = Blueprint("clients", __name__, url_prefix="/api/clients")


def _org_is_firm(conn, org_id: int) -> bool:
    cur = get_db_cursor(conn)
    cur.execute("SELECT org_type FROM organizations WHERE id=%s", (org_id,))
    row = cur.fetchone()
    return row and row.get("org_type") == "firm"


@clients_bp.route("", methods=["GET"])
@require_auth
def list_clients():
    """顧問先一覧を取得"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """SELECT id, name, description, corporate_number, address, phone, industry,
                      employee_count, contact_person, is_active, created_at
               FROM clients WHERE org_id=%s ORDER BY name""",
            (org_id,)
        )
        rows = cur.fetchall()
        conn.close()
        clients = [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r.get("description") or "",
                "corporate_number": r.get("corporate_number") or "",
                "address": r.get("address") or "",
                "phone": r.get("phone") or "",
                "industry": r.get("industry") or "",
                "employee_count": r.get("employee_count"),
                "contact_person": r.get("contact_person") or "",
                "is_active": r["is_active"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
        return success_response(clients)
    except Exception as e:
        logger.error(f"list_clients error: {e}")
        return error_response(str(e), 500)


@clients_bp.route("", methods=["POST"])
@require_auth
def create_client():
    """顧問先を新規登録"""
    try:
        org_id = request.organization_id
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        if not name:
            return error_response("顧問先名は必須です", 400)

        # 必須フィールド確認
        required = ["corporate_number", "address", "phone", "industry", "employee_count", "contact_person"]
        for field in required:
            if not data.get(field):
                return error_response(f"{field} は必須です", 400)

        conn = get_db_connection()
        if not _org_is_firm(conn, org_id):
            conn.close()
            return error_response("この機能は会計事務所プランのみ利用できます", 403)

        cur = get_db_cursor(conn)
        cur.execute(
            """INSERT INTO clients
               (org_id, name, description, corporate_number, address, phone, industry,
                employee_count, contact_person, fiscal_month, tax_type, contract_start_date,
                accountant_name, monthly_fee, bank_name, representative_name, contact_email)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, name, description, corporate_number, address, phone, industry,
                         employee_count, contact_person, is_active, created_at""",
            (org_id, name, (data.get("description") or "").strip(),
             (data.get("corporate_number") or "").strip(),
             (data.get("address") or "").strip(),
             (data.get("phone") or "").strip(),
             (data.get("industry") or "").strip(),
             int(data.get("employee_count") or 0),
             (data.get("contact_person") or "").strip(),
             int(data.get("fiscal_month") or 0) or None,
             (data.get("tax_type") or "").strip() or None,
             data.get("contract_start_date") or None,
             (data.get("accountant_name") or "").strip() or None,
             int(data.get("monthly_fee") or 0) or None,
             (data.get("bank_name") or "").strip() or None,
             (data.get("representative_name") or "").strip() or None,
             (data.get("contact_email") or "").strip() or None)
        )
        row = cur.fetchone()
        conn.commit()
        conn.close()
        return success_response({
            "id": str(row["id"]),
            "name": row["name"],
            "description": row.get("description") or "",
            "corporate_number": row.get("corporate_number") or "",
            "address": row.get("address") or "",
            "phone": row.get("phone") or "",
            "industry": row.get("industry") or "",
            "employee_count": row.get("employee_count"),
            "contact_person": row.get("contact_person") or "",
            "is_active": row["is_active"],
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }, 201)
    except Exception as e:
        logger.error(f"create_client error: {e}")
        return error_response(str(e), 500)


@clients_bp.route("/<client_id>", methods=["PATCH"])
@require_auth
def update_client(client_id):
    """顧問先情報を更新"""
    try:
        org_id = request.organization_id
        data = request.get_json() or {}
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        updates = []
        values = []
        field_map = {
            "name": ("name", str),
            "description": ("description", str),
            "corporate_number": ("corporate_number", str),
            "address": ("address", str),
            "phone": ("phone", str),
            "industry": ("industry", str),
            "employee_count": ("employee_count", int),
            "contact_person": ("contact_person", str),
            "fiscal_month": ("fiscal_month", int),
            "tax_type": ("tax_type", str),
            "contract_start_date": ("contract_start_date", str),
            "accountant_name": ("accountant_name", str),
            "monthly_fee": ("monthly_fee", int),
            "bank_name": ("bank_name", str),
            "representative_name": ("representative_name", str),
            "contact_email": ("contact_email", str),
            "is_active": ("is_active", bool),
        }

        for key, (col, typ) in field_map.items():
            if key in data:
                val = data[key]
                if key == "name" and not (val or "").strip():
                    conn.close()
                    return error_response("顧問先名は必須です", 400)
                if typ == int:
                    val = int(val) if val else None
                elif typ == str:
                    val = (val or "").strip() or None
                updates.append(f"{col}=%s")
                values.append(val)

        if not updates:
            conn.close()
            return error_response("更新するフィールドがありません", 400)

        updates.append("updated_at=NOW()")
        values.extend([client_id, org_id])
        cur.execute(
            f"UPDATE clients SET {', '.join(updates)} WHERE id=%s AND org_id=%s RETURNING id",
            values
        )
        if not cur.fetchone():
            conn.close()
            return error_response("顧問先が見つかりません", 404)
        conn.commit()
        conn.close()
        return success_response({"updated": True})
    except Exception as e:
        logger.error(f"update_client error: {e}")
        return error_response(str(e), 500)


@clients_bp.route("/<client_id>", methods=["DELETE"])
@require_auth
def delete_client(client_id):
    """顧問先を無効化（ソフトデリート）"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "UPDATE clients SET is_active=FALSE, updated_at=NOW() WHERE id=%s AND org_id=%s RETURNING id",
            (client_id, org_id)
        )
        if not cur.fetchone():
            conn.close()
            return error_response("顧問先が見つかりません", 404)
        conn.commit()
        conn.close()
        return success_response({"deleted": True})
    except Exception as e:
        logger.error(f"delete_client error: {e}")
        return error_response(str(e), 500)
