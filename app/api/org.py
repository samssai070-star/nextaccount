"""Organization API — 組織管理"""
from __future__ import annotations
import logging
from flask import Blueprint, request
from .helpers import get_db_connection, get_db_cursor, require_auth, success_response, error_response

logger = logging.getLogger(__name__)
org_bp = Blueprint("org", __name__, url_prefix="/api/org")


@org_bp.route("/update-type", methods=["PATCH"])
@require_auth
def update_org_type():
    """組織タイプを更新（company ↔ firm）"""
    try:
        org_id = request.organization_id
        data = request.get_json() or {}
        org_type = (data.get("org_type") or "").strip().lower()

        if org_type not in ("company", "firm"):
            return error_response("org_type は 'company' または 'firm' である必要があります", 400)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "UPDATE organizations SET org_type=%s, updated_at=NOW() WHERE id=%s RETURNING id",
            (org_type, org_id)
        )
        if not cur.fetchone():
            conn.close()
            return error_response("Organization not found", 404)

        conn.commit()
        conn.close()
        logger.info(f"Org type updated: {org_id} → {org_type}")
        return success_response({"org_type": org_type})
    except Exception as e:
        logger.error(f"update_org_type error: {e}")
        return error_response(str(e), 500)
