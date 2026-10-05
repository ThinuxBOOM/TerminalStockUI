"""pytest bootstrap: import paths + a hermetic environment.

Every test run gets:
  * a throwaway SQLite database (never ``./onemarket.db`` or Supabase),
  * no vendor/AI credentials from the developer's shell,
  * no outbound network (loopback only). Set ``ALLOW_NETWORK_TESTS=1`` to
    opt in for the live-provider smoke tests.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]  # .../backend
ROOT_DIR = BACKEND_DIR.parent

for candidate in (str(ROOT_DIR), str(BACKEND_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

# --- hermetic env (must run before any backend import) ----------------------

_TMP_DIR = tempfile.mkdtemp(prefix="onemarket-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP_DIR, 'test.db').as_posix()}"
os.environ["APP_ENV"] = "test"
os.environ.setdefault("SECRET_KEY", "unit-test-signing-key-3f9c1a7e5b2d8c4f6a0e9b1d")
# Suites log in/register many times from one TestClient address.
os.environ.setdefault("AUTH_RATE_LIMIT_PER_MIN", "0")
os.environ.setdefault("ALLOW_REGISTRATION", "true")

for _name in (
    "REDIS_URL", "UPSTASH_REDIS_URL",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
    "FINNHUB_API_KEY", "TWELVEDATA_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY",
    "CRON_SECRET", "API_KEY",
):
    os.environ.pop(_name, None)

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testserver"}

if os.getenv("ALLOW_NETWORK_TESTS", "").strip() != "1":
    _real_connect = socket.socket.connect
    _real_getaddrinfo = socket.getaddrinfo

    class NetworkBlockedError(OSError):
        pass

    def _host_of(address) -> str:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return str(address)

    def _guarded_connect(self, address):  # type: ignore[no-untyped-def]
        if self.family == getattr(socket, "AF_UNIX", object()) or _host_of(address) in _LOOPBACK:
            return _real_connect(self, address)
        raise NetworkBlockedError(f"network disabled in tests: {address!r}")

    def _guarded_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host is None or str(host) in _LOOPBACK:
            return _real_getaddrinfo(host, *args, **kwargs)
        raise NetworkBlockedError(f"network disabled in tests: {host!r}")

    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]
