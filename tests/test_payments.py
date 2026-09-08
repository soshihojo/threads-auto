import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import payments


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_unit_test_only")
    monkeypatch.setenv("STRIPE_WEBHOOK_LIVEMODE", "false")
    records = []
    monkeypatch.setattr(payments.store, "append_ops_event", lambda r: records.append(r) or True)
    app = FastAPI()
    app.include_router(payments.router)
    return TestClient(app), records


def request(event=None, age=0):
    event = event or {"id": "evt_unit_test", "type": "charge.succeeded", "created": 1788886800,
        "livemode": False, "data": {"object": {"id": "ch_test", "customer": "cus_test",
        "currency": "jpy", "paid": True, "amount_captured": 5980,
        "billing_details": {"email": "should-not-be-stored@example.test"}}}}
    raw = json.dumps(event).encode()
    ts = str(int(time.time()) - age)
    signature = hmac.new(b"whsec_unit_test_only", ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, {"stripe-signature": f"t={ts},v1={signature}"}


def test_valid_signed_event_stores_allowlisted_fields(endpoint):
    client, records = endpoint
    raw, headers = request()
    assert client.post("/stripe/webhook", content=raw, headers=headers).status_code == 200
    assert len(records) == 1
    assert "email" not in records[0]["payload"]
    assert records[0]["id"] == "stripe:evt_unit_test"


@pytest.mark.parametrize("variant", ["tampered", "expired", "missing"])
def test_invalid_signature_never_writes(endpoint, variant):
    client, records = endpoint
    raw, headers = request(age=600 if variant == "expired" else 0)
    if variant == "tampered":
        raw += b" "
    if variant == "missing":
        headers = {}
    assert client.post("/stripe/webhook", content=raw, headers=headers).status_code == 400
    assert not records


def test_storage_failure_is_not_acknowledged(endpoint, monkeypatch):
    client, _ = endpoint
    def fail(r):
        raise RuntimeError("simulated storage outage")
    monkeypatch.setattr(payments.store, "append_ops_event", fail)
    raw, headers = request()
    with pytest.raises(RuntimeError, match="storage outage"):
        client.post("/stripe/webhook", content=raw, headers=headers)


def test_unconfigured_and_wrong_mode_rejected(endpoint, monkeypatch):
    client, records = endpoint
    raw, headers = request()
    monkeypatch.setenv("STRIPE_WEBHOOK_LIVEMODE", "true")
    assert client.post("/stripe/webhook", content=raw, headers=headers).status_code == 400
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET")
    assert client.post("/stripe/webhook", content=raw, headers=headers).status_code == 503
    assert not records
