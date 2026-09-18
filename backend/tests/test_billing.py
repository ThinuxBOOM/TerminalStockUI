"""V2 Phase 4 Stripe billing (TestClient, isolated DB, mocked stripe SDK).

Covers:
  checkout session (metadata user_id, success/cancel URLs)
  webhook sig-fail 400
  checkout.session.completed -> upgrade (via price->tier server map)
  customer.subscription.deleted -> free
  replay idempotent (same event.id twice)
  unknown price -> free + ACK 200 (no retry storm)
"""

from __future__ import annotations

import os
import sys
import types
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api import billing as billing_mod
from backend.api.billing import reset_billing_state, router
from backend.auth.guards import create_access_token
from backend.db.models import Base
from backend.db.session import get_db, get_engine, init_db

TEST_SECRET = "test-billing-secret-0123456789abcdef"


def _install_fake_stripe(calls: dict):
    mod = types.ModuleType("stripe")
    mod.api_key = None  # type: ignore

    class _Customer:
        @staticmethod
        def create(**kwargs):
            calls["customer_create"] = kwargs
            return {"id": "cus_test123"}

    class _CheckoutSession:
        @staticmethod
        def create(**kwargs):
            calls["checkout_create"] = kwargs
            return {"id": "cs_test123", "url": "https://checkout.stripe.com/pay/cs_test123"}

    class _PortalSession:
        @staticmethod
        def create(**kwargs):
            calls["portal_create"] = kwargs
            return {"id": "bps_test123", "url": "https://billing.stripe.com/session/bps_test123"}

    class _Checkout:
        Session = _CheckoutSession

    class _Portal:
        Session = _PortalSession

    class _Subscription:
        @staticmethod
        def retrieve(sub_id):
            calls["sub_retrieve"] = sub_id
            price = calls.get("price_id", os.getenv("STRIPE_PRICE_GOLD", "price_gold"))
            return {"id": sub_id, "status": "active", "items": {"data": [{"price": {"id": price}}]}}

    class _Webhook:
        @staticmethod
        def construct_event(payload, sig_header, secret):
            if calls.get("sig_fail"):
                raise ValueError("bad signature")
            ev = calls.get("event")
            if ev is None:
                raise ValueError("no event staged")
            return ev

    mod.Customer = _Customer  # type: ignore
    mod.checkout = _Checkout  # type: ignore
    mod.billing_portal = _Portal  # type: ignore
    mod.Subscription = _Subscription  # type: ignore
    mod.Webhook = _Webhook  # type: ignore
    sys.modules["stripe"] = mod
    return mod


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_SILVER", "price_silver_test")
    monkeypatch.setenv("STRIPE_PRICE_GOLD", "price_gold_test")
    monkeypatch.setenv("STRIPE_PRICE_PLATINUM", "price_plat_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_123")
    monkeypatch.setenv("FRONTEND_URL", "https://app.test.local")
    from backend.auth import guards as _g  # noqa: F401 ensure users table

    url = f"sqlite:///{tmp_path}/billing.db"
    init_db(url)
    assert "users" in Base.metadata.tables
    Session = sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)

    from backend.auth.guards import get_user_model

    User = get_user_model()
    db = Session()
    try:
        # Gold user starts free (webhook will upgrade); silver starts silver.
        gold_id = str(uuid.uuid4())
        silver_id = str(uuid.uuid4())
        db.add(User(id=uuid.UUID(gold_id), email="gold@test.local", password_hash="x", tier="free", is_admin=False))
        db.add(User(id=uuid.UUID(silver_id), email="silver@test.local", password_hash="x", tier="silver", is_admin=False))
        db.commit()
    finally:
        db.close()

    reset_billing_state()
    billing_mod._USER_STORE.clear()
    billing_mod._PROCESSED_EVENT_IDS.clear()

    app = FastAPI()

    def _override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    gold_token = create_access_token(gold_id, tier="free", is_admin=False)
    silver_token = create_access_token(silver_id, tier="silver", is_admin=False)
    return client, {"gold_id": gold_id, "silver_id": silver_id, "gold": gold_token, "silver": silver_token}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_checkout_creates_session(tmp_path, monkeypatch):
    calls: dict = {}
    _install_fake_stripe(calls)
    client, ids = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/billing/checkout", json={"tier": "gold"}, headers=_auth(ids["gold"]))
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://checkout.stripe.com")
    cc = calls.get("checkout_create", {})
    assert cc.get("mode") == "subscription"
    assert cc.get("metadata", {}).get("user_id") == ids["gold_id"]
    assert cc.get("line_items") == [{"price": "price_gold_test", "quantity": 1}]
    assert "https://app.test.local/checkout/success" in cc.get("success_url", "")
    assert cc.get("cancel_url") == "https://app.test.local/pricing"


def test_webhook_sig_fail_400(tmp_path, monkeypatch):
    calls: dict = {"sig_fail": True}
    _install_fake_stripe(calls)
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "bad"})
    assert r.status_code == 400, r.text


def test_webhook_completed_upgrades_and_deleted_downgrades_idempotent(tmp_path, monkeypatch):
    calls: dict = {}
    _install_fake_stripe(calls)
    client, ids = _make_client(tmp_path, monkeypatch)

    completed = {
        "id": "evt_completed_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test123",
            "customer": "cus_test123",
            "subscription": "sub_test123",
            "metadata": {"user_id": ids["gold_id"]},
        }},
    }
    calls["event"] = completed
    calls["price_id"] = "price_gold_test"
    r = client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "good"})
    assert r.status_code == 200, r.text
    assert r.json().get("received") is True

    s = client.get("/api/billing/status", headers=_auth(ids["gold"]))
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["tier"] == "gold", body
    assert "secret" not in str(body).lower()
    assert "sk_test" not in str(body)

    # Replay same event.id is idempotent.
    r2 = client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "good"})
    assert r2.status_code == 200, r2.text
    assert r2.json().get("duplicate") is True

    deleted = {
        "id": "evt_deleted_1",
        "type": "customer.subscription.deleted",
        "data": {"object": {
            "id": "sub_test123",
            "customer": "cus_test123",
            "status": "canceled",
            "metadata": {"user_id": ids["gold_id"]},
        }},
    }
    calls["event"] = deleted
    r3 = client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "good"})
    assert r3.status_code == 200, r3.text
    s2 = client.get("/api/billing/status", headers=_auth(ids["gold"]))
    assert s2.status_code == 200, s2.text
    assert s2.json()["tier"] == "free", s2.text


def test_webhook_unknown_price_downgrades_free(tmp_path, monkeypatch):
    calls: dict = {}
    _install_fake_stripe(calls)
    client, ids = _make_client(tmp_path, monkeypatch)
    completed = {
        "id": "evt_unknown_price_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_x",
            "customer": "cus_test123",
            "subscription": "sub_x",
            "metadata": {"user_id": ids["silver_id"]},
        }},
    }
    calls["event"] = completed
    calls["price_id"] = "price_unknown_poison"
    r = client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "good"})
    assert r.status_code == 200, r.text
    s = client.get("/api/billing/status", headers=_auth(ids["silver"]))
    assert s.status_code == 200, s.text
    assert s.json()["tier"] == "free", s.text
