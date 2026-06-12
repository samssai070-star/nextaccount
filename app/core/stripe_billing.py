
import os
import stripe
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

def _get_stripe():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY が設定されていません")
    stripe.api_key = key
    return stripe

PRICE_MAP = {
    "starter":  os.environ.get("STRIPE_PRICE_STARTER",  ""),
    "standard": os.environ.get("STRIPE_PRICE_STANDARD", ""),
    "business": os.environ.get("STRIPE_PRICE_BUSINESS", ""),
}

PLAN_NAMES = {
    "starter":  "Starter スタータープラン",
    "standard": "Standard スタンダードプラン",
    "business": "Business ビジネスプラン",
}

def create_checkout_session(plan: str, org_id: int,
                             success_url: str, cancel_url: str) -> str:
    """
    Stripe Checkoutセッションを作成してURLを返す。
    30日間の無料トライアル付き。
    """
    s = _get_stripe()
    price_id = PRICE_MAP.get(plan.lower())
    if not price_id:
        raise ValueError(f"不明なプラン: {plan}")

    session = s.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        subscription_data={
            "trial_period_days": 30,
            "metadata": {
                "org_id": str(org_id),
                "plan": plan,
            },
        },
        automatic_tax={"enabled": True},
        metadata={
            "org_id": str(org_id),
            "plan": plan,
        },
        success_url=success_url,
        cancel_url=cancel_url,
        locale="ja",
    )
    logger.info(f"Checkoutセッション作成: {session.id} plan={plan} org_id={org_id}")
    return session.url


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """
    StripeのWebhookを処理する。
    Returns: {"event_type": str, "tenant_id": str, "plan": str, ...}
    """
    s = _get_stripe()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = s.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        logger.error(f"Webhook署名検証失敗: {e}")
        raise

    event_type = event["type"]
    logger.info(f"Stripe Webhook受信: {event_type}")

    result = {"event_type": event_type}

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        result.update({
            "org_id": session["metadata"].get("org_id", ""),
            "plan": session["metadata"].get("plan", ""),
            "customer_id": session.get("customer", ""),
            "subscription_id": session.get("subscription", ""),
        })

    elif event_type == "customer.subscription.deleted":
        sub = event["data"]["object"]
        result.update({
            "org_id": sub["metadata"].get("org_id", ""),
            "subscription_id": sub["id"],
            "status": "canceled",
        })

    elif event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        result.update({
            "customer_id": invoice.get("customer", ""),
            "subscription_id": invoice.get("subscription", ""),
            "status": "payment_failed",
        })

    elif event_type == "customer.subscription.trial_will_end":
        sub = event["data"]["object"]
        result.update({
            "org_id": sub["metadata"].get("org_id", ""),
            "subscription_id": sub["id"],
            "trial_end": sub.get("trial_end"),
        })

    elif event_type == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        result.update({
            "customer_id": invoice.get("customer", ""),
            "subscription_id": invoice.get("subscription", ""),
            "amount_paid": invoice.get("amount_paid", 0),
            "status": "paid",
        })

    return result


def get_subscription_status(subscription_id: str) -> dict:
    """サブスクリプションの現在状態を取得する"""
    s = _get_stripe()
    try:
        sub = s.Subscription.retrieve(subscription_id)
        return {
            "status": sub.status,
            "trial_end": sub.trial_end,
            "current_period_end": sub.current_period_end,
            "plan": sub.metadata.get("plan", ""),
        }
    except Exception as e:
        logger.error(f"サブスクリプション取得失敗: {e}")
        return {"status": "unknown"}


def cancel_subscription(subscription_id: str) -> bool:
    """サブスクリプションをキャンセルする（期末まで有効）"""
    s = _get_stripe()
    try:
        s.Subscription.modify(subscription_id, cancel_at_period_end=True)
        logger.info(f"サブスクリプションキャンセル設定: {subscription_id}")
        return True
    except Exception as e:
        logger.error(f"キャンセル失敗: {e}")
        return False
