"""Alert delivery hooks (Phase 3b).

:class:`ConsoleNotifier` (default, no deps) logs the redacted event JSON.
:class:`WebhookNotifier` POSTs redacted JSON to ``ALERTS_WEBHOOK_URL`` when
set (5s timeout); every failure is swallowed with a warning and reported
as ``False`` so delivery can never block alert firing.

Email via Resend is explicitly OUT OF SCOPE (follow-up). Env contract:
``RESEND_API_KEY`` + ``ALERTS_FROM`` (sender identity). No new deps here.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

WEBHOOK_ENV = "ALERTS_WEBHOOK_URL"
WEBHOOK_TIMEOUT_S = 60.0


def _redacted(event: dict) -> dict:
    """Redacted copy of an alert event (secrets never reach logs/webhooks)."""
    try:
        from backend.security.secrets import redact_mapping
    except Exception:
        return dict(event or {})
    try:
        return redact_mapping(dict(event or {}))
    except Exception:
        return dict(event or {})


class ConsoleNotifier:
    """Default notifier: logs redacted JSON (no deps, never raises)."""

    name = "console"

    def notify(self, event: dict) -> bool:
        try:
            logger.info(
                "alert notification %s",
                json.dumps(_redacted(event or {}), default=str),
            )
        except Exception:
            logger.warning("alert console delivery failed")
        return True


class WebhookNotifier:
    """POST redacted JSON to ``ALERTS_WEBHOOK_URL`` (no-op when unset)."""

    name = "webhook"

    def __init__(
        self, url: str | None = None, timeout_s: float = WEBHOOK_TIMEOUT_S
    ) -> None:
        raw = url if url is not None else os.getenv(WEBHOOK_ENV, "")
        self.url = (raw or "").strip()
        try:
            self.timeout_s = float(timeout_s or WEBHOOK_TIMEOUT_S)
        except (TypeError, ValueError):
            self.timeout_s = WEBHOOK_TIMEOUT_S

    def notify(self, event: dict) -> bool:
        if not self.url:
            return False  # no-op: webhook not configured
        try:
            import httpx  # lazy: optional at import time; offline stays offline
        except Exception:
            logger.warning("alerts webhook skipped (httpx unavailable)")
            return False
        try:
            resp = httpx.post(
                self.url, json=_redacted(event or {}), timeout=self.timeout_s
            )
            resp.raise_for_status()
            return True
        except Exception:
            logger.warning("alerts webhook delivery failed")
            return False


def get_notifier():
    """Default hook: webhook when ``ALERTS_WEBHOOK_URL`` is set, else console."""
    url = (os.getenv(WEBHOOK_ENV, "") or "").strip()
    if url:
        return WebhookNotifier(url)
    return ConsoleNotifier()


__all__ = [
    "WEBHOOK_ENV",
    "WEBHOOK_TIMEOUT_S",
    "ConsoleNotifier",
    "WebhookNotifier",
    "get_notifier",
]
