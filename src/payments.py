"""Stripe signed-event intake; recording only, never auto-enroll or message."""
from datetime import datetime, timezone
import json

import stripe
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import store
from .config import env
from .operations import new_event

router = APIRouter()
SUPPORTED = {"charge.succeeded", "charge.captured", "refund.created", "refund.updated",
             "invoice.paid", "invoice.payment_failed", "customer.subscription.created",
             "customer.subscription.updated", "customer.subscription.deleted"}


def _id(obj):
    return obj.get("id", "") if isinstance(obj, dict) else (obj or "")


def normalize(event):
    if event.get("type") not in SUPPORTED:
        return None
    obj = event["data"]["object"]
    # Allowlist excludes billing names, addresses, email and card details.
    fields = ("status", "currency", "amount", "amount_captured", "paid", "amount_paid",
              "cancel_at_period_end", "cancel_at", "canceled_at", "current_period_end")
    p = {key: obj[key] for key in fields if key in obj}
    p.update(type=event["type"], object_id=obj["id"], customer_id=_id(obj.get("customer")),
             subscription_id=_id(obj.get("subscription")), livemode=event["livemode"])
    created = datetime.fromtimestamp(event["created"], timezone.utc).isoformat()
    return new_event("", "stripe.receipt", p, event_id="stripe:" + event["id"], created_at=created)


@router.post("/stripe/webhook")
async def webhook(request: Request):
    secret = env("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "Stripe intake is not configured")
    raw = await request.body()
    if len(raw) > 1_000_000:
        raise HTTPException(413, "Payload too large")
    try:
        stripe.Webhook.construct_event(raw, request.headers.get("stripe-signature", ""), secret)
        event = json.loads(raw)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(400, "Invalid Stripe signature or payload")
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid event structure")
    live = env("STRIPE_WEBHOOK_LIVEMODE") == "true"
    if bool(event.get("livemode")) != live:
        raise HTTPException(400, "Stripe mode does not match configured mode")
    try:
        record = normalize(event)
    except (KeyError, ValueError, TypeError, OverflowError):
        raise HTTPException(400, "Invalid event structure")
    if record is None:
        return {"received": True, "ignored": True}
    # Acknowledge only after durable storage. Failures return 5xx for Stripe retry.
    added = await run_in_threadpool(store.append_ops_event, record)
    return {"received": True, "duplicate": not added}
