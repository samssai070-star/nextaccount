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
    {"id": "starter",  "name": "Starter",  "price_jpy": 980,  "receipts": 30,  "description": "30枚/月・人数無制限"},
    {"id": "standard", "name": "Standard", "price_jpy": 1980, "receipts": 100, "description": "100枚/月・人数無制限"},
    {"id": "business", "name": "Business", "price_jpy": 5980, "receipts": -1,  "description": "枚数無制限・人数無制限"},
]

_VALID_PLANS = {p["id"] for p in PLAN_INFO}


def ensure_billing_columns():
    """organizations テーブルに課金カラムを追加（冪等・既存カラムはスキップ）"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE organizations
            ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255),
            ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255),
            ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(50) DEFAULT 'trial',
            ADD COLUMN IF NOT EXISTS plan VARCHAR(50),
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
            return error_response("無効なプランです", 400)

        org_id = request.organization_id
        success_url = f"{BASE_URL}/dashboard.html"
        cancel_url = f"{BASE_URL}/dashboard.html"

        # 既存の Stripe 顧客 ID を取得（あればカード再入力不要）
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT stripe_customer_id FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()
        conn.close()
        existing_customer_id = org.get("stripe_customer_id") if org else None

        from core.stripe_billing import create_checkout_session
        checkout_url = create_checkout_session(plan, org_id, success_url, cancel_url, existing_customer_id)

        return success_response({"checkout_url": checkout_url})

    except RuntimeError as e:
        logger.warning(f"Stripe not configured: {e}")
        return error_response("Stripe 課金が設定されていません", 503)
    except Exception as e:
        logger.error(f"create_checkout error: {e}")
        return error_response(str(e), 500)


@billing_bp.route("/manage-card", methods=["POST"])
@require_auth
def manage_card():
    """カード未登録 → Setup Checkout、登録済み → カスタマーポータル"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT stripe_customer_id FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()
        conn.close()

        from core.stripe_billing import _get_stripe
        s = _get_stripe()
        customer_id = org.get("stripe_customer_id") if org else None

        if customer_id:
            # 登録済み → Setup Checkout でカード追加／変更
            session = s.checkout.Session.create(
                mode="setup",
                customer=customer_id,
                payment_method_types=["card"],
                success_url=f"{BASE_URL}/dashboard.html?card=updated",
                cancel_url=f"{BASE_URL}/dashboard.html",
                locale="ja",
            )
        else:
            # 未登録 → Customer を先に作成してから Setup Checkout
            customer = s.Customer.create(metadata={"org_id": str(org_id)})
            conn2 = get_db_connection()
            cur2 = get_db_cursor(conn2)
            cur2.execute(
                "UPDATE organizations SET stripe_customer_id=%s WHERE id=%s",
                (customer.id, org_id)
            )
            conn2.commit()
            conn2.close()

            session = s.checkout.Session.create(
                mode="setup",
                customer=customer.id,
                success_url=f"{BASE_URL}/dashboard.html?card=registered",
                cancel_url=f"{BASE_URL}/dashboard.html",
                locale="ja",
            )
        return success_response({"url": session.url})

    except RuntimeError as e:
        return error_response("Stripe 課金が設定されていません", 503)
    except Exception as e:
        logger.error(f"manage_card error: {e}")
        return error_response(str(e), 500)


@billing_bp.route("/status", methods=["GET"])
@require_auth
def get_billing_status():
    """現在の課金状態を取得"""
    try:
        org_id = request.organization_id
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """SELECT subscription_status, plan, trial_ends_at,
                      stripe_customer_id, stripe_subscription_id
               FROM organizations WHERE id=%s""",
            (org_id,)
        )
        org = cur.fetchone()
        conn.close()

        if not org:
            return error_response("Organization not found", 404)

        return success_response({
            "billing_status": org.get("subscription_status") or "trial",
            "billing_plan": org.get("plan"),
            "trial_ends_at": org["trial_ends_at"].isoformat() if org.get("trial_ends_at") else None,
            "has_subscription": bool(org.get("stripe_subscription_id")),
        })

    except Exception as e:
        logger.error(f"get_billing_status error: {e}")
        return error_response(str(e), 500)
