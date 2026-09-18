"""Stripe billing router (V2 Phase 4). Prefix /api/billing.

Endpoints:
    POST /api/billing/checkout {tier: silver|gold|platinum} -> {url}
    POST /api/billing/portal -> {url}
    POST /api/billing/webhook (Stripe-signed, raw body) -> {received: true}
    GET  /api/billing/status -> {tier, subscription_status, stripe_customer_id?}

Auth: checkout/portal/status require a live user (get_current_user).
Webhook is unauthenticated by design: Stripe HMAC signature
(stripe.Webhook.construct_event with STRIPE_WEBHOOK_SECRET) is the auth.
Never trust client-supplied tier for DB writes: price->tier mapping is
server-side only, from STRIPE_PRICE_* env.

Mount (do NOT edit backend/api/main.py here; snippet returned separately):
    from backend.api.billing import router as billing_router
    app.include_router(billing_router)
Webhook exempt note: /api/billing/webhook must bypass any API-key check
(see backend/security/auth.py is_open_path / middleware) — raw-body HMAC
verify replaces the key. If API_KEY enforcement is enabled, add
"/api/billing/webhook" to the open path set.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])

VALID_CHECKOUT_TIERS = ("silver", "gold", "platinum")

#: Idempotency: Stripe may redeliver events. Processed event.id values are
#: remembered process-locally (convergent UPDATEs make replays safe even
#: across restarts; audit dedupe is best-effort).
_PROCESSED_EVENT_IDS: set[str] = set()

#: Test/dev fallback user store when the users table/ORM is unavailable
#: (Phase 1 lands separately). Keyed by user_id -> {tier, stripe_customer_id,
#: stripe_subscription_id, subscription_status, email}.
_USER_STORE: dict[str, dict[str, Any]] = {}


def reset_billing_state() -> None:  # test hook
    _PROCESSED_EVENT_IDS.clear()
    _USER_STORE.clear()


# --- auth (guards with fail-closed fallback) ---------------------------------
try:  # Phase 2 canonical
    from backend.auth.guards import get_current_user  # type: ignore
except ImportError:  # pragma: no cover - fallback until Phase 2 lands
    from fastapi import Request as _Request

    from backend.auth.tiers import _TIER_RANK as _RANK
    from backend.auth.tiers import normalize_tier as _norm

    _TEST_TOKENS: dict[str, dict[str, Any]] = {
        "test-free": {"user_id": "user-free", "tier": "free", "is_admin": False, "email": "free@test.local"},
        "test-silver": {"user_id": "user-silver", "tier": "silver", "is_admin": False, "email": "silver@test.local"},
        "test-gold": {"user_id": "user-gold", "tier": "gold", "is_admin": False, "email": "gold@test.local"},
        "test-platinum": {"user_id": "user-platinum", "tier": "platinum", "is_admin": False, "email": "platinum@test.local"},
        "test-admin": {"user_id": "admin-1", "tier": "platinum", "is_admin": True, "email": "admin@test.local"},
    }

    async def get_current_user(request: _Request) -> dict[str, Any]:  # type: ignore[no-redef]
        """Fallback auth: Bearer test tokens only. X-Tier is NEVER trusted."""
        try:
            auth = (request.headers.get("authorization") or "").strip()
        except Exception:
            auth = ""
        token = ""
        if auth[:7].lower() == "bearer ":
            token = auth[7:].strip()
        # Also accept x-api-key style? No — Bearer only, fail-closed.
        user = _TEST_TOKENS.get(token)
        if user is not None:
            return dict(user)
        raise HTTPException(status_code=401, detail="unauthorized")


def _user_field(user: Any, name: str, default: Any = None) -> Any:
    """Read user field from dict (fallback) or ORM (guards) users."""
    try:
        if isinstance(user, dict):
            return user.get(name, default)
    except Exception:
        pass
    try:
        val = getattr(user, name, default)
        return default if val is None and default is not None else val
    except Exception:
        return default


def _frontend_url() -> str:
    return (os.getenv("FRONTEND_URL", "") or "").strip().rstrip("/") or "http://localhost:5173"


def _price_map() -> dict[str, str]:
    """Server-side price->tier map. Never accept tier from client/webhook body."""
    mapping: dict[str, str] = {}
    try:
        silver = (os.getenv("STRIPE_PRICE_SILVER", "") or "").strip()
        gold = (os.getenv("STRIPE_PRICE_GOLD", "") or "").strip()
        plat = (os.getenv("STRIPE_PRICE_PLATINUM", "") or "").strip()
    except Exception:
        silver = gold = plat = ""
    if silver:
        mapping[silver] = "silver"
    if gold:
        mapping[gold] = "gold"
    if plat:
        mapping[plat] = "platinum"
    return mapping


def _tier_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    return _price_map().get(str(price_id).strip())


def _stripe() -> Any:
    """Lazy stripe import (mockable via sys.modules['stripe'] in tests)."""
    try:
        import stripe as _s  # type: ignore

        return _s
    except ImportError as exc:
        raise HTTPException(status_code=502, detail="billing unavailable: stripe SDK not installed") from exc


def _db_session() -> Any | None:
    try:
        from backend.db.session import get_session_factory

        Session = get_session_factory()
        return Session()
    except Exception:
        return None


def _row_to_record(row: Any, user_id: str) -> dict[str, Any]:
    try:
        return {
            "user_id": str(getattr(row, "id", user_id)),
            "email": getattr(row, "email", None),
            "tier": str(getattr(row, "tier", "free") or "free"),
            "stripe_customer_id": getattr(row, "stripe_customer_id", None),
            "stripe_subscription_id": getattr(row, "stripe_subscription_id", None),
            "subscription_status": getattr(row, "subscription_status", None),
        }
    except Exception:
        return {"user_id": user_id, "tier": "free", "stripe_customer_id": None,
                "stripe_subscription_id": None, "subscription_status": None, "email": None}


def _load_user_with_db(db: Any, user_id: str) -> dict[str, Any] | None:
    """Load users row using the request-scoped db (respects test overrides)."""
    if db is None:
        return None
    try:
        from backend.db.models import User as _User  # type: ignore

        row = None
        try:
            row = db.query(_User).filter(_User.id == user_id).first()  # type: ignore
        except Exception:
            row = None
        if row is None:
            try:
                import uuid as _uuid

                row = db.query(_User).filter(_User.id == _uuid.UUID(str(user_id))).first()  # type: ignore
            except Exception:
                row = None
        if row is not None:
            return _row_to_record(row, user_id)
    except Exception:
        pass
    return None


def _save_user_with_db(db: Any, user_id: str, fields: dict[str, Any]) -> bool:
    """Update users row using the request-scoped db. Returns True on write."""
    if db is None:
        return False
    try:
        from backend.db.models import User as _User  # type: ignore

        row = None
        try:
            row = db.query(_User).filter(_User.id == user_id).first()  # type: ignore
        except Exception:
            row = None
        if row is None:
            try:
                import uuid as _uuid

                row = db.query(_User).filter(_User.id == _uuid.UUID(str(user_id))).first()  # type: ignore
            except Exception:
                row = None
        if row is None:
            return False
        for k, v in fields.items():
            try:
                if hasattr(row, k):
                    setattr(row, k, v)
            except Exception:
                continue
        try:
            from datetime import datetime, timezone as _tz

            if hasattr(row, "updated_at"):
                row.updated_at = datetime.now(_tz.utc)  # type: ignore
        except Exception:
            pass
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _load_user_record(user_id: str, db: Any = None) -> dict[str, Any]:
    """Best-effort load of users row. Falls back to in-memory store.

    When a request-scoped db is passed (checkout/portal/status/webhook with
    Depends(get_db)), it is used first so test overrides apply. Otherwise a
    short-lived session is opened. Memory is always the last fallback.
    """
    # 0) Request-scoped db first (respects dependency_overrides in tests).
    if db is not None:
        try:
            rec = _load_user_with_db(db, user_id)
            if rec is not None:
                return rec
        except Exception:
            pass
    # 1) Try ORM via short-lived session.
    owned = None
    try:
        from backend.db.models import User as _User  # type: ignore

        owned = _db_session()
        if owned is not None:
            try:
                rec = _load_user_with_db(owned, user_id)
                if rec is not None:
                    return rec
            finally:
                try:
                    owned.close()
                except Exception:
                    pass
                owned = None
    except Exception:
        try:
            if owned is not None:
                owned.close()
        except Exception:
            pass
    # 2) In-memory fallback (tests / pre-Phase-1 dev).
    if user_id in _USER_STORE:
        rec = dict(_USER_STORE[user_id])
        rec.setdefault("user_id", user_id)
        return rec
    return {"user_id": user_id, "tier": "free", "stripe_customer_id": None,
            "stripe_subscription_id": None, "subscription_status": None, "email": None}


def _save_user_record(user_id: str, db: Any = None, **fields: Any) -> dict[str, Any]:
    """Best-effort persist. Request db first, then short-lived session, always memory."""
    # Update memory first (tests assert on this even when DB is absent).
    rec = dict(_USER_STORE.get(user_id, {"user_id": user_id}))
    rec.update({k: v for k, v in fields.items() if v is not None or k in fields})
    _USER_STORE[user_id] = rec
    # 0) Request-scoped db (respects test overrides). Commit here; caller
    # must NOT close it (owned by the request).
    if db is not None:
        try:
            _save_user_with_db(db, user_id, fields)
        except Exception:
            pass
    # 1) Short-lived session fallback (production path when no db passed,
    # e.g. webhook without Depends(get_db)).
    if db is None:
        owned = None
        try:
            owned = _db_session()
            if owned is not None:
                try:
                    _save_user_with_db(owned, user_id, fields)
                finally:
                    try:
                        owned.close()
                    except Exception:
                        pass
        except Exception:
            try:
                if owned is not None:
                    owned.close()
            except Exception:
                pass
    merged = dict(rec)
    merged["user_id"] = user_id
    return merged


def _audit_subscription_changed(actor: str, user_id: str, payload: dict[str, Any], db: Any = None) -> None:
    """Redacted audit for subscription changes. Never raises, never logs secrets."""
    owned = None
    use_db = db
    try:
        from backend.api.audit import append_audit_log
        from backend.security.secrets import redact_mapping

        if use_db is None:
            try:
                use_db = _db_session()
                owned = use_db
            except Exception:
                use_db = None
        if use_db is None:
            return
        try:
            append_audit_log(
                use_db,
                actor=actor,
                action="subscription.changed",
                entity_type="users",
                entity_id=str(user_id),
                payload=redact_mapping(dict(payload or {})),
            )
        except Exception as exc:
            logger.warning("billing audit missed user=%s: %s", str(user_id)[:8], type(exc).__name__)
        finally:
            try:
                if owned is not None:
                    owned.close()
            except Exception:
                pass
    except Exception:
        try:
            if owned is not None:
                owned.close()
        except Exception:
            pass


class CheckoutBody(BaseModel):
    tier: str = Field(description="silver|gold|platinum")


from backend.db.session import get_db as _get_db_dep  # request-scoped DB (test overrides apply)


@router.post("/checkout")
async def post_checkout(
    body: CheckoutBody,
    request: Request,
    user: Any = Depends(get_current_user),
    db: Any = Depends(_get_db_dep),
) -> dict[str, Any]:
    want = (body.tier or "").strip().lower()
    if want not in VALID_CHECKOUT_TIERS:
        raise HTTPException(status_code=422, detail=f"tier must be one of {list(VALID_CHECKOUT_TIERS)}, got {body.tier!r}")
    price_map_inv: dict[str, str] = {}
    try:
        inv = {v: k for k, v in _price_map().items()}
        price_id = inv.get(want, "")
    except Exception:
        price_id = ""
    if not price_id:
        raise HTTPException(status_code=502, detail=f"billing not configured for tier {want!r} (missing STRIPE_PRICE_* env)")
    user_id = str(_user_field(user, "user_id", None) or _user_field(user, "id", "") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    email = _user_field(user, "email", None)
    stored = _load_user_record(user_id, db)
    if not email:
        email = stored.get("email")
    customer_id = stored.get("stripe_customer_id")
    s = _stripe()
    try:
        api_key = (os.getenv("STRIPE_SECRET_KEY", "") or "").strip() or None
        if api_key and hasattr(s, "api_key"):
            try:
                s.api_key = api_key
            except Exception:
                pass
        if not customer_id:
            try:
                kwargs: dict[str, Any] = {"metadata": {"user_id": user_id}}
                if email:
                    # Prefer explicit email link; fall back to metadata-only.
                    kwargs["email"] = email
                created = s.Customer.create(**kwargs)
                customer_id = created.get("id") if isinstance(created, dict) else getattr(created, "id", None)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"stripe customer create failed: {type(exc).__name__}") from exc
            if customer_id:
                _save_user_record(user_id, db, stripe_customer_id=customer_id)
        front = _frontend_url()
        try:
            session = s.checkout.Session.create(
                customer=customer_id,
                customer_email=None if customer_id else (email or None),
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                metadata={"user_id": user_id},
                success_url=f"{front}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{front}/pricing",
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"stripe checkout create failed: {type(exc).__name__}") from exc
        url = session.get("url") if isinstance(session, dict) else getattr(session, "url", None)
        if not url:
            raise HTTPException(status_code=502, detail="stripe checkout create failed: missing url")
        return {"url": url}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"billing checkout failed: {type(exc).__name__}") from exc


@router.post("/portal")
async def post_portal(
    request: Request,
    user: Any = Depends(get_current_user),
    db: Any = Depends(_get_db_dep),
) -> dict[str, Any]:
    user_id = str(_user_field(user, "user_id", None) or _user_field(user, "id", "") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    stored = _load_user_record(user_id, db)
    customer_id = stored.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=422, detail="no stripe customer yet: complete checkout first")
    s = _stripe()
    try:
        api_key = (os.getenv("STRIPE_SECRET_KEY", "") or "").strip() or None
        if api_key and hasattr(s, "api_key"):
            try:
                s.api_key = api_key
            except Exception:
                pass
        front = _frontend_url()
        try:
            session = s.billing_portal.Session.create(customer=customer_id, return_url=f"{front}/account")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"stripe portal create failed: {type(exc).__name__}") from exc
        url = session.get("url") if isinstance(session, dict) else getattr(session, "url", None)
        if not url:
            raise HTTPException(status_code=502, detail="stripe portal create failed: missing url")
        return {"url": url}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"billing portal failed: {type(exc).__name__}") from exc


@router.get("/status")
async def get_status(
    request: Request,
    user: Any = Depends(get_current_user),
    db: Any = Depends(_get_db_dep),
) -> dict[str, Any]:
    user_id = str(_user_field(user, "user_id", None) or _user_field(user, "id", "") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    stored = _load_user_record(user_id, db)
    # Live tier prefers the users row; fall back to the JWT tier claim.
    # _load_user_record falls back to memory when the users row is missing,
    # so prefer a real DB tier only when present; else use the live JWT claim.
    db_tier = stored.get("tier") if isinstance(stored, dict) else None
    # Memory fallback seeds tier=free even when no row exists — ignore that
    # when the JWT already carries a live tier (avoids masking upgrades).
    jwt_tier = _user_field(user, "tier", "free")
    if db_tier in (None, "", "free") and jwt_tier and str(jwt_tier).strip().lower() != "free":
        # If the in-memory store has no real row for this user, trust JWT.
        try:
            has_mem = user_id in _USER_STORE and _USER_STORE[user_id].get("tier") not in (None, "", "free")
            tier = str(db_tier) if has_mem else str(jwt_tier)
        except Exception:
            tier = str(jwt_tier)
    else:
        tier = str(db_tier or jwt_tier or "free")
    tier = tier.strip().lower() or "free"
    out: dict[str, Any] = {"tier": tier, "subscription_status": stored.get("subscription_status")}
    if stored.get("stripe_customer_id"):
        out["stripe_customer_id"] = stored.get("stripe_customer_id")
    # Never leak secrets (no api keys, no subscription secrets).
    return out


def _extract_price_id_from_subscription(sub: Any) -> str | None:
    try:
        if isinstance(sub, dict):
            items = (sub.get("items") or {}).get("data") or []
            if items and isinstance(items[0], dict):
                price = items[0].get("price") or {}
                if isinstance(price, dict) and price.get("id"):
                    return str(price["id"])
                if isinstance(price, str):
                    return price
        else:
            items = getattr(getattr(sub, "items", None), "data", None) or []
            if items:
                price = getattr(items[0], "price", None)
                if isinstance(price, dict) and price.get("id"):
                    return str(price["id"])
                pid = getattr(price, "id", None)
                if pid:
                    return str(pid)
    except Exception:
        pass
    return None


def _extract_session_fields(session_obj: Any) -> tuple[str | None, str | None, str | None]:
    """Return (user_id, customer_id, subscription_id) from a checkout session."""
    user_id = customer_id = subscription_id = None
    try:
        if isinstance(session_obj, dict):
            meta = session_obj.get("metadata") or {}
            user_id = meta.get("user_id") or session_obj.get("client_reference_id")
            customer_id = session_obj.get("customer")
            subscription_id = session_obj.get("subscription")
        else:
            meta = getattr(session_obj, "metadata", None) or {}
            try:
                user_id = meta.get("user_id") if isinstance(meta, dict) else getattr(meta, "user_id", None)
            except Exception:
                user_id = None
            customer_id = getattr(session_obj, "customer", None)
            subscription_id = getattr(session_obj, "subscription", None)
    except Exception:
        pass
    # Normalize Stripe object refs that may be dicts {id: ...}.
    for name in ("customer_id", "subscription_id"):
        pass
    try:
        if isinstance(customer_id, dict):
            customer_id = customer_id.get("id")
        if isinstance(subscription_id, dict):
            subscription_id = subscription_id.get("id")
    except Exception:
        pass
    return (str(user_id) if user_id else None,
            str(customer_id) if customer_id else None,
            str(subscription_id) if subscription_id else None)


@router.post("/webhook")
async def post_webhook(request: Request, db: Any = Depends(_get_db_dep)) -> dict[str, Any]:
    try:
        payload = await request.body()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid webhook payload")
    try:
        sig_header = request.headers.get("stripe-signature", "") or ""
    except Exception:
        sig_header = ""
    secret = (os.getenv("STRIPE_WEBHOOK_SECRET", "") or "").strip()
    s = _stripe()
    try:
        construct = getattr(getattr(s, "Webhook", None), "construct_event", None)
        if construct is None:
            raise HTTPException(status_code=502, detail="billing unavailable: stripe webhook verify missing")
        try:
            event = construct(payload, sig_header, secret)
        except TypeError:
            # Some SDK doubles accept (payload_str, sig, secret).
            try:
                event = construct(payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload, sig_header, secret)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"webhook signature failed: {type(exc).__name__}") from exc
    except HTTPException as exc:
        # Signature failures must be 400 (not 401/502) per contract.
        if exc.status_code == 400:
            raise
        # Non-signature HTTP errors from missing SDK propagate.
        raise
    except Exception as exc:
        # stripe.error.SignatureVerificationError + ValueError -> 400.
        raise HTTPException(status_code=400, detail=f"webhook signature failed: {type(exc).__name__}") from exc
    # Normalize event to dict-like.
    try:
        if isinstance(event, dict):
            event_id = str(event.get("id") or "")
            event_type = str(event.get("type") or "")
            data_obj = ((event.get("data") or {}).get("object")) if isinstance(event.get("data"), dict) else None
        else:
            event_id = str(getattr(event, "id", "") or "")
            event_type = str(getattr(event, "type", "") or "")
            data = getattr(event, "data", None)
            data_obj = getattr(data, "object", None) if data is not None else None
    except Exception:
        raise HTTPException(status_code=400, detail="invalid webhook event")
    if not event_id:
        raise HTTPException(status_code=400, detail="invalid webhook event: missing id")
    if event_id in _PROCESSED_EVENT_IDS:
        return {"received": True, "duplicate": True}
    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(event, data_obj, db)
        elif event_type == "customer.subscription.updated":
            _handle_subscription_updated(event, data_obj, db)
        elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
            _handle_subscription_deleted(event, data_obj, db)
        else:
            # Unknown types are ACKed (return 200) so Stripe stops retrying.
            # Subscription status-driven downgrades arrive as updated/deleted;
            # anything else (e.g. invoice.*) needs no tier change.
            # Past-due / incomplete-expired surface as updated with those
            # statuses — handled inside _handle_subscription_updated.
            logger.info("billing webhook ignored type=%s id=%s", event_type, event_id[:12])
        _PROCESSED_EVENT_IDS.add(event_id)
        return {"received": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("billing webhook handler failed id=%s: %s", event_id[:12], type(exc).__name__)
        raise HTTPException(status_code=500, detail="webhook handler failed") from exc


def _handle_checkout_completed(event: Any, session_obj: Any, db: Any = None) -> None:
    user_id, customer_id, subscription_id = _extract_session_fields(session_obj)
    price_id: str | None = None
    # Direct price hint (tests + some Checkout payloads).
    try:
        if isinstance(session_obj, dict):
            price_id = session_obj.get("price_id") or session_obj.get("price") or None
            # line_items shape fallback.
            if not price_id:
                li = session_obj.get("line_items")
                if isinstance(li, list) and li and isinstance(li[0], dict):
                    price_id = li[0].get("price")
        else:
            price_id = getattr(session_obj, "price_id", None)
    except Exception:
        price_id = None
    sub: Any = None
    if subscription_id:
        try:
            s = _stripe()
            try:
                sub = s.Subscription.retrieve(subscription_id)
            except Exception:
                sub = None
        except Exception:
            sub = None
    if not price_id and sub is not None:
        price_id = _extract_price_id_from_subscription(sub)
    # Last resort: event-level price hint (test doubles).
    if not price_id:
        try:
            if isinstance(event, dict):
                data = event.get("data") or {}
                obj = data.get("object") or {}
                if isinstance(obj, dict):
                    price_id = obj.get("price_id")
        except Exception:
            pass
    tier = _tier_for_price(str(price_id) if price_id else None)
    if tier is None:
        logger.warning("billing unknown price id=%s price=%r -> free", str((event.get('id') if isinstance(event, dict) else getattr(event, 'id', '')))[:12], price_id)
        tier = "free"
    if not user_id:
        # No user to upgrade — ACK without a write (never crash the webhook).
        logger.warning("billing checkout completed without user_id; ACK")
        return
    status = "active"
    try:
        if isinstance(sub, dict):
            status = str(sub.get("status") or "active")
        elif sub is not None:
            status = str(getattr(sub, "status", "active") or "active")
    except Exception:
        status = "active"
    # past_due / incomplete_expired on completion -> free (fail-closed).
    if status in ("past_due", "incomplete_expired", "incomplete", "unpaid"):
        tier = "free"
    _save_user_record(
        str(user_id), db, tier=tier,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        subscription_status=status,
    )
    _audit_subscription_changed("system", str(user_id), {
        "event": "checkout.session.completed", "tier": tier,
        "status": status,
        "customer": (str(customer_id)[:8] + "…" if customer_id else None),
        "subscription": (str(subscription_id)[:8] + "…" if subscription_id else None),
    }, db)


def _handle_subscription_updated(event: Any, sub_obj: Any, db: Any = None) -> None:
    # Extract customer / status / price.
    customer_id = status = price_id = None
    sub_id = None
    try:
        if isinstance(sub_obj, dict):
            customer_id = sub_obj.get("customer")
            status = sub_obj.get("status")
            sub_id = sub_obj.get("id")
            price_id = _extract_price_id_from_subscription(sub_obj)
            meta = sub_obj.get("metadata") or {}
            user_hint = meta.get("user_id")
        else:
            customer_id = getattr(sub_obj, "customer", None)
            status = getattr(sub_obj, "status", None)
            sub_id = getattr(sub_obj, "id", None)
            price_id = _extract_price_id_from_subscription(sub_obj)
            try:
                meta = getattr(sub_obj, "metadata", None) or {}
                user_hint = meta.get("user_id") if isinstance(meta, dict) else getattr(meta, "user_id", None)
            except Exception:
                user_hint = None
    except Exception:
        user_hint = None
    try:
        if isinstance(customer_id, dict):
            customer_id = customer_id.get("id")
    except Exception:
        pass
    status = str(status or "active")
    # Resolve user by stripe_customer_id (reverse lookup) or metadata hint.
    user_id = str(user_hint) if user_hint else _find_user_by_customer(str(customer_id) if customer_id else None, db)
    if status in ("past_due", "incomplete_expired", "incomplete", "unpaid", "canceled", "incomplete_expired"):
        tier = "free"
    else:
        tier = _tier_for_price(str(price_id) if price_id else None)
        if tier is None:
            logger.warning("billing unknown price on update id=%s price=%r -> free", str(sub_id)[:12], price_id)
            tier = "free"
    if not user_id:
        logger.warning("billing subscription updated without user; ACK")
        return
    _save_user_record(
        str(user_id), db, tier=tier,
        stripe_customer_id=str(customer_id) if customer_id else None,
        stripe_subscription_id=str(sub_id) if sub_id else None,
        subscription_status=status,
    )
    _audit_subscription_changed("system", str(user_id), {
        "event": "customer.subscription.updated", "tier": tier, "status": status,
    }, db)


def _handle_subscription_deleted(event: Any, sub_obj: Any, db: Any = None) -> None:
    customer_id = sub_id = status = None
    user_hint = None
    try:
        if isinstance(sub_obj, dict):
            customer_id = sub_obj.get("customer")
            sub_id = sub_obj.get("id")
            status = sub_obj.get("status") or "canceled"
            user_hint = ((sub_obj.get("metadata") or {}).get("user_id"))
        else:
            customer_id = getattr(sub_obj, "customer", None)
            sub_id = getattr(sub_obj, "id", None)
            status = getattr(sub_obj, "status", None) or "canceled"
            try:
                meta = getattr(sub_obj, "metadata", None) or {}
                user_hint = meta.get("user_id") if isinstance(meta, dict) else getattr(meta, "user_id", None)
            except Exception:
                user_hint = None
    except Exception:
        pass
    try:
        if isinstance(customer_id, dict):
            customer_id = customer_id.get("id")
    except Exception:
        pass
    user_id = str(user_hint) if user_hint else _find_user_by_customer(str(customer_id) if customer_id else None, db)
    if not user_id:
        logger.warning("billing subscription deleted without user; ACK")
        return
    status = str(status or "canceled")
    _save_user_record(str(user_id), db, tier="free", subscription_status=status,
                      stripe_subscription_id=str(sub_id) if sub_id else None)
    _audit_subscription_changed("system", str(user_id), {
        "event": "customer.subscription.deleted", "tier": "free", "status": status,
    }, db)


def _find_user_by_customer(customer_id: str | None, db: Any = None) -> str | None:
    if not customer_id:
        return None
    # Memory store first.
    for uid, rec in _USER_STORE.items():
        try:
            if rec.get("stripe_customer_id") == customer_id:
                return uid
        except Exception:
            continue
    # Request db first (test overrides).
    if db is not None:
        try:
            from backend.db.models import User as _User  # type: ignore

            row = db.query(_User).filter(_User.stripe_customer_id == customer_id).first()  # type: ignore
            if row is not None:
                return str(getattr(row, "id", ""))
        except Exception:
            pass
    # Short-lived fallback.
    try:
        owned = _db_session()
        if owned is not None:
            try:
                from backend.db.models import User as _User2  # type: ignore

                row = owned.query(_User2).filter(_User2.stripe_customer_id == customer_id).first()  # type: ignore
                if row is not None:
                    return str(getattr(row, "id", ""))
            finally:
                try:
                    owned.close()
                except Exception:
                    pass
    except Exception:
        pass
    return None


__all__ = ["router", "reset_billing_state"]
