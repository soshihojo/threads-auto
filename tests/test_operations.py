import json
from datetime import datetime, timezone

from src.operations import (acknowledgements, billing_alerts, financials, message_key, new_event,
                            task_board, unacknowledged)

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 1, tzinfo=timezone.utc)


def evt(kind, data, eid="one", uid="u1", at="2026-09-08T10:00:00+00:00"):
    return new_event(uid, kind, data, event_id=eid, created_at=at)


def charge(eid="one", **changes):
    return evt("stripe.receipt", {"type": "charge.succeeded", "object_id": "ch_test",
        "customer_id": "cus_test", "livemode": True, "paid": True,
        "currency": "jpy", "amount_captured": 5980, **changes}, eid=eid)


def test_retry_and_separate_events_do_not_double_count():
    r = charge()
    report = financials([r, r, charge("two", type="charge.captured")], START, END)
    assert report["receipts"] == 5980
    assert report["unlinked"] == 1


def test_refund_without_original_charge_never_becomes_a_sale():
    r = evt("stripe.receipt", {"type": "refund.created", "object_id": "re_test",
            "status": "succeeded", "amount": 1000, "currency": "jpy", "livemode": True})
    update = {**r, "id": "two"}
    result = financials([r, update], START, END)
    assert (result["receipts"], result["refunds"], result["net"]) == (0, 1000, -1000)


def test_tests_uncaptured_and_foreign_currency_are_excluded():
    result = financials([charge("test", livemode=False), charge("unpaid", paid=False),
                         charge("auth", amount_captured=0), charge("usd", currency="usd")], START, END)
    assert result["receipts"] == 0
    assert result["other_currency"] == ["usd"]


def test_prior_month_capture_does_not_reappear_and_customer_link_is_retroactive():
    earlier = {**charge(), "created_at": "2026-08-31T10:00:00+00:00"}
    linked = evt("customer.link", {"customer_id": "cus_test"}, eid="link")
    assert financials([earlier, charge("two")], START, END)["receipts"] == 0
    assert financials([charge(), linked], START, END)["unlinked"] == 0


def test_manual_receipts_dedupe_and_void_with_replacement():
    p = {"provider": "STORES", "reference": "test-order", "amount_yen": 3980}
    r = evt("payment.manual", p)
    duplicate = evt("payment.manual", p, eid="two")
    assert financials([r, duplicate], START, END)["receipts"] == 3980
    void = evt("payment.void", {"event_id": "one"}, eid="void")
    assert financials([r, void], START, END)["receipts"] == 0


def test_manual_ack_only_covers_selected_customer_and_inbound_ids():
    chats = [{"id": 10, "role": "user"}, {"id": 11, "role": "user"}]
    rows = [evt("reply.ack", {"chat_id": "10", "chat_keys": [message_key(chats[0])]})]
    ack = acknowledgements(rows)
    assert unacknowledged(chats, ack["u1"]) == [chats[1]]
    assert len(unacknowledged(chats, ack.get("u2"))) == 2
    assert unacknowledged(chats + [{"id": 12, "role": "assistant"}], ack["u1"]) == []
    assert len(unacknowledged([{"role": "user"}], {message_key({"role": "user"})})) == 1
    reused_id = {"id": 10, "role": "user", "text": "後から届いた別の相談"}
    assert unacknowledged([reused_id], ack["u1"]) == [reused_id]


def test_multiple_tasks_for_one_customer_are_independent():
    a = evt("task.set", {"task_id": "a", "status": "open"})
    b = evt("task.set", {"task_id": "b", "status": "open"}, eid="two")
    done = evt("task.set", {"task_id": "a", "status": "done"}, eid="three", at="2026-09-09T00:00:00Z")
    board = task_board([done, a, b, b])
    assert [(t["task_id"], t["status"]) for t in board] == [("b", "open"), ("a", "done")]


def test_invoice_recovery_and_out_of_order_events():
    p = {"type": "invoice.payment_failed", "object_id": "in_test", "livemode": True}
    failed = evt("stripe.receipt", p)
    paid = evt("stripe.receipt", {**p, "type": "invoice.paid"}, eid="paid", at="2026-09-09T00:00:00Z")
    assert billing_alerts([paid, failed]) == []
    assert len(billing_alerts([failed])) == 1
    assert financials([paid, failed], START, END)["receipts"] == 0


def test_sqlite_ledger_is_persistent_and_idempotent(tmp_path, monkeypatch):
    from src import store_sqlite
    monkeypatch.setattr(store_sqlite, "DB_PATH", tmp_path / "test.db")
    store_sqlite.init_db()
    store_sqlite.init_db()
    row = evt("work.log", {"minutes": 12})
    assert store_sqlite.append_ops_event(row)
    assert not store_sqlite.append_ops_event(row)
    assert store_sqlite.list_ops_events() == [row]
    mid = store_sqlite.add_member("架空会員", "2000-01-01", "2000-01-02", line_user_id="u1")
    assert dict(store_sqlite.list_members()[0])["line_user_id"] == "u1"
    assert store_sqlite.set_member_line_user(mid, "u2")


def test_sheets_retry_duplicates_collapse_without_modifying_other_tables(monkeypatch):
    from src import store_sheets
    saved = []
    monkeypatch.setattr(store_sheets, "_records", lambda name: saved if name == "ops_events" else [])
    monkeypatch.setattr(store_sheets, "_append", lambda name, row: saved.extend([row, row]))
    row = charge()
    assert store_sheets.append_ops_event(row)
    assert not store_sheets.append_ops_event(row)
    assert financials(store_sheets.list_ops_events(), START, END)["receipts"] == 5980
