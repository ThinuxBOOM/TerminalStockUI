"""Observability package (M0/M3-M4): provider metrics, dashboard data, audit verification."""

from backend.observability.audit_verify import verify_chain, verify_rows
from backend.observability.dashboard import build_dashboard
from backend.observability.provider_metrics import (
    aggregate_all,
    aggregate_provider_calls,
    check_error_alert,
    record_call,
)

__all__ = [
    "aggregate_all",
    "aggregate_provider_calls",
    "build_dashboard",
    "check_error_alert",
    "record_call",
    "verify_chain",
    "verify_rows",
]
