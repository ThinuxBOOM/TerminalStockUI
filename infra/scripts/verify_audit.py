#!/usr/bin/env python3
"""Audit hash-chain verifier alias (stdlib-only wrapper).

Canonical verifier lives at ``backend/observability/audit_verify.py``
(``python -m backend.observability.audit_verify``). This alias exists
because README/docs reference ``infra/scripts/verify_audit.py`` — it
delegates to the canonical module so both paths stay green and can never
drift apart.

Usage (repo root)::

    python infra/scripts/verify_audit.py [--database-url URL]
    ALLOW_MISSING_DB=1 python infra/scripts/verify_audit.py  # warn, don't fail, when DB file missing

Exit 0 when the chain verifies, 1 on any gap/rewrite, 2 on usage errors
(missing backend module, missing DB file without ALLOW_MISSING_DB).
Stdlib-only: argparse + subprocess + sys + pathlib.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve()
    # infra/scripts/verify_audit.py -> parents[2] is repo root.
    if len(here.parents) >= 3 and (here.parents[2] / "backend").is_dir():
        return here.parents[2]
    if here.parent.name == "scripts" and here.parent.parent.is_dir():
        return here.parent.parent
    return Path.cwd()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the OneMarket audit hash chain (alias for backend.observability.audit_verify)."
    )
    parser.add_argument("--root", default=None, help="repo root (default: auto-detect)")
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///./onemarket.db)",
    )
    args, passthrough = parser.parse_known_args(argv)
    root = repo_root(args.root)
    if not root.is_dir():
        print("FAIL: repo root not found: %s" % root, file=sys.stderr)
        return 2
    target = root / "backend" / "observability" / "audit_verify.py"
    if not target.is_file():
        print(
            "FAIL: canonical verifier missing: %s "
            "(expected backend/observability/audit_verify.py under root %s)"
            % (target, root),
            file=sys.stderr,
        )
        return 2
    cmd = [sys.executable, "-m", "backend.observability.audit_verify"]
    if args.database_url:
        cmd += ["--database-url", args.database_url]
    cmd += passthrough
    try:
        proc = subprocess.run(cmd, cwd=str(root))
    except OSError as exc:
        print(
            "FAIL: cannot launch audit verifier (%s: %s)" % (type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 2
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
