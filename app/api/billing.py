"""Billing API Routes"""
from __future__ import annotations
import os, logging
from flask import Blueprint, request
from .helpers import (
    get_db_connection, get_db_cursor, require_auth,
    success_response, error_response
)

logger = logging.getLogger(__name__)
billing_bp = Blueprint("billing", __name__, url_prefix="/api/billing")

BASE_URL = os.environ.get("BASE_URL", "https://nextaccount.jp")

PLAN_INFO = [
    {"id": "micro",      "name": "Micro",      "price_jpy": 2980,  "users": 3,   "description": "小規模チーム向け（最大3名）"},
    {"id": "starter",    "name": "Starter",    "price_jpy": 5980,  "users": 5,   "description": "スタートアップ向け（最大5名）"},
    {"id": "business",   "name": "Business",   "price_jpy": 14800, "users": 15,  "description": "中小企業向け（最大15名）"},
    {"id": "growth",     "name": "Growth",     "price_jpy": 24800, "users": 25,  "description": "成長企業向け（最大25名）"},
    {"id": "enterprise", "name": "Enterprise", "price_jpy": 49800, "users": 999, "description": "大企業向け（無制限）"},
]

_VALID_PLANS = {p["id"] for p in PLAN_INFO}


def ensure_billing_columns():
    """organizations テーブルに課金カラムを追加（冪等）"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE organizations
            ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255),
            ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255),
            ADD COLUMN IF NOT EXISTS billing_status VARCHAR(50) DEFAULT 'trial',
            ADD COLUMN IF NOT EXISTS billing_plan VARCHAR(50),
            ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP
        """)
        conn.commit()
        conn.close()
        logger.info("billing columns ensured")
    except Exception as e:
        logger.warning(f"ensure_billing_columns: {e}")


@billing_bp.route("/plans", methods=["GET"])
def get_plans():
    """利用可能なプラン一覧を返す"""
    return success_response(PLAN_INFO)


@billing_bp.route("/create-checkout", methods=["POST"])
@require_auth
def create_checkout():
    """Stripe Checkout セッションを作成してURLを返す"""
    try:
        data = request.get_json() or {}
        plan = (data.get("plan") or "").lower().strip()

        if plan not in _VALID_PLANS:
            return error_response("無効なプランです"), 400

        org_id = request.organization_id
        success_url = f"{BASE_URL}/dashboard.html"
        cancel_url = f"{BASE_URL}/setup.html?step=5"

        from core.stripe_billing import create_checkout_session
        checkout_url = create_checkout_session(plan, org_id, success_url, cancel_url)

        return success_response({"checkout_url": checkout_url})

    except RuntimeError as e:
        logger.warning(f"Stripe not configured: {e}")
        return error_response("Stripe 課金が設定されていません"), 503
    except Exception as e:
        logger.error(f"create_checkout error: {e}")
        return error_response(str(e)), 500


@billing_bp.route("/status", methods=["GET"])
@require_auth
def get_billing_status():
    """現在の課金状態を取得"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """SELECT billing_status, billing_plan, trial_ends_at,
                      stripe_customer_id, stripe_subscription_id
               FROM organizations WHERE id=%s""",
            (org_id,)
        )
        org = cur.fetchone()
        conn.close()

        if not org:
            return error_response("Organization not found"), 404

        return success_response({
            "billing_status": org.get("billing_status") or "trial",
            "billing_plan": org.get("billing_plan"),
            "trial_ends_at": org["trial_ends_at"].isoformat() if org.get("trial_ends_at") else None,
            "has_subscription": bool(org.get("stripe_subscription_id")),
        })

    except Exception as e:
        logger.error(f"get_billing_status error: {e}")
        return error_response(str(e)), 500
