"""Operations ledger. No message sending, billing changes, or inferred payments."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
STAGES = ("入金確認", "ヒアリング待ち", "鑑定作成", "納品待ち", "感想確認", "会員対応", "決済確認", "その他")
KINDS = {"task.set", "reply.ack", "work.log", "customer.link", "stripe.receipt", "payment.manual", "payment.void"}


def new_event(user_id: str, kind: str, payload: dict, *, event_id: str = "", created_at: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError("Unknown operations event")
    return {"id": event_id or str(uuid4()), "user_id": str(user_id), "kind": kind,
            "created_at": created_at or datetime.now(JST).isoformat(timespec="microseconds"),
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}


def events(rows):
    """Sheet appends may be retried. Collapse identical IDs before every projection."""
    seen = set()
    result = []
    for raw in rows:
        r = dict(raw)
        if not r.get("id") or r["id"] in seen:
            continue
        seen.add(r["id"])
        try:
            r["data"] = json.loads(r["payload"])
        except (ValueError, TypeError, KeyError):
            continue
        if isinstance(r["data"], dict):
            result.append(r)
    return sorted(result, key=lambda r: (instant(r["created_at"]), r["id"]))


def instant(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=JST)).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def acknowledgements(rows):
    out = {}
    for r in events(rows):
        if r["kind"] == "reply.ack":
            keys = r["data"].get("chat_keys", [])
            if isinstance(keys, list):
                out.setdefault(r["user_id"], set()).update(k for k in keys if isinstance(k, str))
    return out


def message_key(chat):
    """IDs alone are not unique under concurrent Sheets writes."""
    return hashlib.sha256(json.dumps([str(chat.get(k) or "") for k in ("id", "created_at", "text")],
                                   ensure_ascii=False).encode()).hexdigest()


def unacknowledged(chats, acknowledged=None):
    """Only a trailing inbound burst; acknowledge a specific observed message."""
    pending = []
    for raw in reversed(chats):
        r = dict(raw)
        if r.get("role") != "user":
            break
        covered = bool(r.get("id")) and message_key(r) in (acknowledged or set())
        if not covered:
            pending.append(r)
    return list(reversed(pending))


def task_board(rows):
    tasks = {}
    for r in events(rows):
        if r["kind"] == "task.set" and r["data"].get("task_id"):
            p = r["data"]
            tasks[p["task_id"]] = {**p, "user_id": r["user_id"], "updated_at": r["created_at"]}
    return sorted(tasks.values(), key=lambda t: (t.get("status") == "done", t.get("due_at") or "9999"))


def customer_links(rows):
    result = {}
    for r in events(rows):
        if r["kind"] == "customer.link" and r["data"].get("customer_id"):
            result[r["data"]["customer_id"]] = r["user_id"]
    return result


def financials(rows, start, end):
    """Cash receipts/refunds, NOT profit. Interval [start, end); JPY only.

    Charge captures are cumulative; refunds are identified by refund ID.
    Retries and separate event IDs cannot double count. Invoice events do not add
    revenue. Unlinked Stripe receipts remain in totals and are shown separately.
    """
    parsed = events(rows)
    voided = {r["data"].get("event_id") for r in parsed if r["kind"] == "payment.void"}
    links = customer_links(rows)
    charges, refunds, manual_refs = {}, set(), set()
    receipts = refund_total = work_minutes = unlinked = 0
    other_currency = set()
    for r in parsed:
        p = r["data"]
        when = instant(r["created_at"])
        in_period = start <= when < end
        if r["kind"] == "work.log" and in_period:
            work_minutes += max(0, int(p.get("minutes", 0)))
        if r["kind"] == "payment.manual":
            if r["id"] in voided:
                continue
            ref = (p.get("provider"), p.get("reference"))
            if not all(ref) or ref in manual_refs:
                continue
            manual_refs.add(ref)
            if in_period:
                receipts += max(0, int(p.get("amount_yen", 0)))
            continue
        if r["kind"] != "stripe.receipt" or not p.get("type", "").startswith(("charge.", "refund.")):
            continue
        if p.get("livemode") is not True:
            continue
        if p.get("currency") != "jpy":
            if in_period:
                other_currency.add(p.get("currency") or "不明")
            continue
        cid = p["object_id"]
        if p["type"].startswith("refund."):
            if p.get("status") == "succeeded" and cid not in refunds:
                refunds.add(cid)
                if in_period:
                    refund_total += max(0, int(p.get("amount", 0)))
            continue
        captured = max(0, int(p.get("amount_captured", 0))) if p.get("paid") else 0
        delta = max(0, captured - charges.get(cid, 0))
        charges[cid] = max(charges.get(cid, 0), captured)
        if in_period:
            receipts += delta
            if delta and not links.get(p.get("customer_id")):
                unlinked += 1
    return {"receipts": receipts, "refunds": refund_total, "net": receipts - refund_total,
            "minutes": work_minutes, "unlinked": unlinked, "other_currency": sorted(other_currency)}


def billing_alerts(rows):
    latest = {}
    for r in events(rows):
        if r["kind"] != "stripe.receipt":
            continue
        p = r["data"]
        if p.get("livemode") is not True:
            continue
        # Charge failure is not sufficient to decide a subscription is unpaid.
        if p["type"].startswith(("invoice.", "customer.subscription.")):
            key = p["object_id"]
            # When timestamps tie, retain the terminal/success state.
            priority = int(p["type"] in {"invoice.paid", "customer.subscription.deleted"})
            rank = (instant(r["created_at"]), priority)
            if key not in latest or rank >= latest[key][0]:
                latest[key] = (rank, p)
    return [p for _, p in latest.values() if p["type"] == "invoice.payment_failed"
            or p.get("status") in {"past_due", "unpaid", "canceled", "incomplete_expired"}
            or p.get("cancel_at_period_end")]
