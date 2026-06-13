"""Organization API — 組織管理"""
from __future__ import annotations
import os
import logging
from flask import Blueprint, request
from .helpers import get_db_connection, get_db_cursor, require_auth, success_response, error_response

logger = logging.getLogger(__name__)
org_bp = Blueprint("org", __name__, url_prefix="/api/org")

ORG_UPGRADE_CODE = os.environ.get("ORG_UPGRADE_CODE", "")


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

        # 会計事務所への変更は授権コード必須
        if org_type == "firm":
            if not ORG_UPGRADE_CODE:
                return error_response("プラットフォームで授権コードが設定されていません", 503)
            if not auth_code:
                return error_response("授権コードが必要です", 400)
            if auth_code != ORG_UPGRADE_CODE:
                return error_response("授権コードが無効です", 403)

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # 既に firm になっている場合はスキップ
        cur.execute("SELECT org_type FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()
        if not org:
            conn.close()
            return error_response("Organization not found", 404)
        if org["org_type"] == "firm" and org_type == "firm":
            conn.close()
            return success_response({"org_type": "firm", "message": "既に会計事務所です"})

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
