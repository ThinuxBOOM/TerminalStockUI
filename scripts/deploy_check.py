"""OneMarket Analyzer deploy preflight (GitHub + Vercel + Supabase).

Stdlib-only: no third-party imports (``yaml`` is used best-effort if already
installed, otherwise a structural fallback validates the workflow).

Owned by the CI/docs agent: .github/, docs/DEPLOY_VERCEL_SUPABASE.md, scripts/.
Files owned by other agents (vercel.json, api/index.py, supabase/migrations/)
report SKIP — not FAIL — when absent; the Vercel-dashboard path in
docs/DEPLOY_VERCEL_SUPABASE.md is normative until they land.

Usage (repo root)::

    python scripts/deploy_check.py
    python scripts/deploy_check.py --root <path-to-onemarket-analyzer>

Exit 0 unless any REQUIRED check FAILs (SKIP is honest, not fatal).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_TABLES = ("instruments", "price_bars", "forecasts", "audit_logs")
# Every key the code actually reads: backend/db/session.py (DATABASE_URL,
# APP_ENV), backend/cache.py (REDIS_URL/UPSTASH_REDIS_URL), backend/security
# (SECRET_KEY), backend/api/cron.py (CRON_SECRET), frontend/src/api (VITE_*).
REQUIRED_ENV_KEYS = ("DATABASE_URL", "SECRET_KEY", "CRON_SECRET", "APP_ENV",
                     "VITE_API_BASE_URL", "REDIS_URL")
# Backend cron routes served by backend/api/cron.py (GET /api/cron/*).
# Vercel covers ingest+calibrate only: Hobby plans cap cron slots at two,
# so /api/cron/evaluate runs via GitHub Actions (.github/workflows/alerts.yml).
EXPECTED_VERCEL_CRON_PATHS = ("/api/cron/ingest", "/api/cron/calibrate")


def repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve()
    if here.parent.name == "scripts" and here.parent.parent.is_dir():
        return here.parent.parent
    return Path.cwd()


def read_text(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError:
        return None


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, list[str]]] = []

    def add(self, status: str, title: str, evidence: list[str]) -> None:
        assert status in ("PASS", "SKIP", "FAIL")
        self.rows.append((status, title, evidence))

    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "SKIP": 0, "FAIL": 0}
        for status, _, _ in self.rows:
            out[status] += 1
        return out


def check_ci_yaml(root: Path, rep: Report) -> None:
    """REQUIRED: workflow exists, YAML parses, covers push/PR + pytest + typecheck."""
    rel = ".github/workflows/ci.yml"
    text = read_text(root, rel)
    if text is None:
        rep.add("FAIL", "ci workflow exists", ["missing: " + rel])
        return
    detail = [rel + " present"]
    try:
        import yaml  # type: ignore  # best-effort: present in most Pythons/CIs

        data = yaml.safe_load(text)
        if not isinstance(data, dict) or "jobs" not in data:
            rep.add("FAIL", "ci workflow yaml valid", ["top-level mapping lacks 'jobs'"])
            return
        # NOTE: YAML 1.1 parses the key `on:` as boolean True (PyYAML gotcha).
        on = data.get("on", data.get(True, []))
        triggers = set(on if isinstance(on, list) else on.keys() if isinstance(on, dict) else [on])
        jobs = data.get("jobs", {})
        job_text = json.dumps(jobs)
        missing = [t for t in ("push", "pull_request") if t not in triggers]
        if missing:
            rep.add("FAIL", "ci workflow triggers", ["missing triggers: " + ", ".join(missing)])
            return
        detail += ["triggers: " + ", ".join(sorted(triggers)),
                   "jobs: " + ", ".join(sorted(jobs))]
        need = {"pytest backend": "pytest" in job_text,
                "typecheck frontend": "typecheck" in job_text,
                "pip/npm cache": ("setup-python" in job_text and "setup-node" in job_text)}
    except ImportError:
        if "\t" in text:
            rep.add("FAIL", "ci workflow yaml valid", ["literal tab found (yaml forbids tabs)"])
            return
        need = {"on: push/PR": ("push" in text and "pull_request" in text),
                "pytest backend": ("pytest" in text and "backend/tests" in text),
                "typecheck frontend": "typecheck" in text,
                "pip/npm cache": ("setup-python" in text and "setup-node" in text)}
        detail += ["note: pyyaml absent, structural check only"]
    bad = [k for k, v in need.items() if not v]
    if bad:
        rep.add("FAIL", "ci workflow covers pytest + typecheck + cache", detail + ["missing: " + ", ".join(bad)])
    else:
        rep.add("PASS", "ci workflow (push/PR, pytest, typecheck, pip/npm cache)",
                detail + ["covers: " + ", ".join(sorted(need))])


def check_gitignore(root: Path, rep: Report) -> None:
    """REQUIRED: .vercel/, *.db, .env excluded (existing entries preserved)."""
    text = read_text(root, ".gitignore")
    if text is None:
        rep.add("FAIL", ".gitignore present", ["missing: .gitignore"])
        return
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    need = {".vercel/": any(ln in (".vercel/", ".vercel") for ln in lines),
            "*.db": any(ln in ("*.db", "**/*.db") for ln in lines),
            ".env": any(ln == ".env" or (ln.startswith(".env") and "*" in ln) for ln in lines)}
    bad = [k for k, v in need.items() if not v]
    if bad:
        rep.add("FAIL", ".gitignore excludes deploy secrets/artifacts",
                ["missing patterns: " + ", ".join(bad)])
    else:
        rep.add("PASS", ".gitignore excludes .vercel/ + *.db + .env",
                ["patterns present: " + ", ".join(sorted(need))])


def check_deploy_doc(root: Path, rep: Report) -> None:
    """REQUIRED: end-to-end doc with all 6 numbered sections."""
    rel = "docs/DEPLOY_VERCEL_SUPABASE.md"
    text = read_text(root, rel)
    if text is None:
        rep.add("FAIL", "deploy doc exists", ["missing: " + rel])
        return
    sections = re.findall(r"^##\s+([1-6])\.\s*(.+)$", text, re.M)
    nums = {n for n, _ in sections}
    keywords = {"gh repo create": "gh repo create" in text,
                "supabase migration 0001": "0001_initial.sql" in text,
                "pooled DATABASE_URL": "pooler" in text or "6543" in text,
                "vercel build frontend/dist": "frontend/dist" in text,
                "serverless limits/cron": "Cron" in text,
                "verify /health + forecast": ("/health" in text and "/api/forecast/AAPL" in text),
                "rollback": "rollback" in text.lower() or "Rollback" in text}
    missing_kw = [k for k, v in keywords.items() if not v]
    if nums != {"1", "2", "3", "4", "5", "6"}:
        rep.add("FAIL", "deploy doc has 6 sections",
                ["found sections: " + (", ".join(n for n, _ in sections) or "none")])
    elif missing_kw:
        rep.add("FAIL", "deploy doc covers git/supabase/vercel/limits/verify/rollback",
                ["missing content: " + ", ".join(missing_kw)])
    else:
        rep.add("PASS", "deploy doc (6 sections end-to-end)",
                ["sections: " + ", ".join(n + ". " + t for n, t in sections)])


def check_vercel_json(root: Path, rep: Report) -> None:
    """ADVISORY: vercel.json validated if present; SKIP if dashboard path used."""
    for rel in ("vercel.json", "frontend/vercel.json"):
        text = read_text(root, rel)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            rep.add("FAIL", "vercel.json parses", ["%s: %s" % (rel, exc)])
            return
        detail = ["%s parses (%d keys)" % (rel, len(data))]
        problems: list[str] = []
        # Rewrites must route /api/* and /health to the serverless entry.
        # Accepted destinations: "/api" (directory-index resolution to
        # api/index.py, the deployed scheme per docs/API_CONTRACT.md) or
        # "/api/index.py" (explicit file target). Either reaches the Mangum
        # app; only a missing route is a failure.
        rewrites = data.get("rewrites", [])
        if isinstance(rewrites, list) and rewrites:
            dests = {str(r.get("destination", "")) for r in rewrites
                     if isinstance(r, dict)}
            srcs = {str(r.get("source", "")) for r in rewrites
                    if isinstance(r, dict)}
            ok_dests = {"/api", "/api/index.py"}
            if "/api/(.*)" in srcs or "/api/:path*" in srcs:
                if not (dests & ok_dests):
                    problems.append(
                        "rewrite /api/(.*) must target /api or /api/index.py "
                        "(got destinations: %s)" % sorted(dests))
            if "/health" in srcs and not (dests & ok_dests):
                problems.append("rewrite /health must target /api or /api/index.py")
            detail.append("rewrites: %s -> %s" % (sorted(srcs), sorted(dests)))
        # Vercel crons must cover ingest+calibrate. /api/cron/evaluate is
        # intentionally NOT a Vercel cron (Hobby 2-slot cap) — it runs via
        # GitHub Actions, checked separately below.
        crons = data.get("crons", [])
        if isinstance(crons, list) and crons:
            paths = {str(c.get("path", "")) for c in crons if isinstance(c, dict)}
            missing_crons = [p for p in EXPECTED_VERCEL_CRON_PATHS if p not in paths]
            if missing_crons:
                problems.append(
                    "crons missing backend routes: " + ", ".join(missing_crons)
                    + " (vercel.json must declare "
                    + ", ".join(EXPECTED_VERCEL_CRON_PATHS) + ")")
            detail.append("crons: " + ", ".join(sorted(paths)))
        if problems:
            rep.add("FAIL", "vercel.json rewrites/crons match backend",
                    detail + ["mismatch: " + p for p in problems])
        else:
            rep.add("PASS", "vercel.json present + valid JSON", detail)
        check_alerts_workflow(root, rep)
        return
    rep.add("SKIP", "vercel.json (dashboard import is normative; file pending Vercel agent)",
            ["no vercel.json at root or frontend/; build/output configured in dashboard per docs §3"])


def check_alerts_workflow(root: Path, rep: Report) -> None:
    """GitHub Actions must cover /api/cron/evaluate (not a Vercel cron)."""
    text = read_text(root, ".github/workflows/alerts.yml")
    if text is None:
        rep.add("FAIL", "alerts workflow covers /api/cron/evaluate",
                ["missing .github/workflows/alerts.yml"])
        return
    if "/api/cron/evaluate" in text and "schedule" in text:
        rep.add("PASS", "alerts workflow covers /api/cron/evaluate",
                ["GitHub Actions schedules evaluate (Vercel Hobby cap: 2 crons)"])
    else:
        rep.add("FAIL", "alerts workflow covers /api/cron/evaluate",
                ["alerts.yml must schedule /api/cron/evaluate"])


def check_api_entry(root: Path, rep: Report) -> None:
    """ADVISORY: api/index.py validated if present; SKIP until backend/Vercel agent lands it."""
    text = read_text(root, "api/index.py")
    if text is None:
        rep.add("SKIP", "api/index.py (pending backend/Vercel agent)",
                ["absent; Vercel rewrite /api/(.*) target documented in docs §3"])
        return
    has_import = bool(re.search(
        r"from\s+backend\.|import\s+backend\b|backend\.app|api\.main|FastAPI", text))
    # Vercel discovers file-based functions via static analysis: `app` must be
    # a top-level name (indented/try-wrapped imports are invisible to it and
    # the build fails with "pattern ... doesn't match any Serverless Functions").
    has_top_level_app = bool(re.search(
        r"^(?:from\s+\S+\s+import\s+[^\n]*\bapp\b|app\s*=)", text, re.M))
    # Legacy Mangum adapter (accepted but no longer required: the current
    # Python runtime serves the FastAPI `app` natively).
    has_handler = "handler" in text and "Mangum" in text
    has_lifespan_off = 'lifespan="off"' in text or "lifespan='off'" in text
    if not has_import:
        rep.add("FAIL", "api/index.py imports backend app",
                ["no backend import / FastAPI app reference found"])
    elif not has_top_level_app:
        rep.add("FAIL", "api/index.py exposes top-level app",
                ["`app` must be a top-level `from backend.api.main import app` "
                 "(try/except-wrapped imports are invisible to Vercel and cause "
                 "'pattern ... doesn't match any Serverless Functions')"])
    elif has_handler and not has_lifespan_off:
        rep.add("FAIL", "api/index.py Mangum handler",
                ["legacy Mangum handler must use lifespan='off' (or drop Mangum: "
                 "the current runtime serves the FastAPI `app` natively)"])
    else:
        rep.add("PASS", "api/index.py imports backend app",
                ["entry wires the FastAPI app",
                 "top-level app for Vercel static discovery",
                 "handler = Mangum(app, lifespan='off')"
                 if has_handler else "native runtime (no Mangum adapter needed)"])


def check_migration_tables(root: Path, rep: Report) -> None:
    """REQUIRED: a migration (supabase/ preferred, infra/ fallback) has all 4 tables."""
    candidates = sorted((root / "supabase" / "migrations").glob("*.sql"))
    if not candidates and (root / "infra" / "migrations" / "0001_initial.sql").is_file():
        candidates = [root / "infra" / "migrations" / "0001_initial.sql"]
    if not candidates:
        rep.add("FAIL", "supabase migration present",
                ["no supabase/migrations/*.sql and no infra/migrations/0001_initial.sql"])
        return
    blob = ""
    for path in candidates:
        try:
            blob += path.read_text(encoding="utf-8") + "\n"
        except OSError:
            pass
    have = [t for t in REQUIRED_TABLES
            if re.search(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?" + t + r"\b", blob, re.I)]
    missing = [t for t in REQUIRED_TABLES if t not in have]
    which = ", ".join(p.name for p in candidates)
    # Advisory: last-fetched quote persistence (0005) — outages serve stored
    # market data instead of placeholders when present.
    snapshot = "present" if re.search(
        r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?quote_snapshots\b", blob, re.I) \
        else "absent (outage fallback degrades to placeholders)"
    if missing:
        rep.add("FAIL", "migration defines 4 v1 tables",
                ["files: " + which, "missing tables: " + ", ".join(missing)])
    else:
        rep.add("PASS", "migration defines 4 v1 tables (%s)" % which,
                ["tables: " + ", ".join(have),
                 "quote_snapshots persistence: " + snapshot])


def check_frontend_dist(root: Path, rep: Report) -> None:
    """REQUIRED: build/typecheck scripts + vite config (output frontend/dist)."""
    pkg_text = read_text(root, "frontend/package.json")
    if pkg_text is None:
        rep.add("FAIL", "frontend package.json present", ["missing: frontend/package.json"])
        return
    try:
        pkg = json.loads(pkg_text)
    except json.JSONDecodeError as exc:
        rep.add("FAIL", "frontend package.json parses", [str(exc)])
        return
    scripts = pkg.get("scripts", {})
    build = scripts.get("build", "")
    ok = {"build runs vite build": "vite build" in build,
          "typecheck script": "typecheck" in scripts,
          "vite.config.ts": (root / "frontend" / "vite.config.ts").is_file()}
    bad = [k for k, v in ok.items() if not v]
    if bad:
        rep.add("FAIL", "frontend dist config", ["missing: " + ", ".join(bad)])
    else:
        rep.add("PASS", "frontend dist config (build -> frontend/dist)",
                ["build: %r; typecheck: %r" % (build, scripts.get("typecheck"))])


def check_env_example(root: Path, rep: Report) -> None:
    """REQUIRED: env examples document every key the code reads.

    backend keys (infra/docker/.env.example): DATABASE_URL, REDIS_URL,
    SECRET_KEY, CRON_SECRET, APP_ENV. frontend key (frontend/.env.example):
    VITE_API_BASE_URL (+ APP_ENV mirror). Missing files/keys FAIL with the
    exact path so operators know where to add them (never crash).
    """
    infra_text = read_text(root, "infra/docker/.env.example")
    if infra_text is None:
        rep.add("FAIL", "env example present", ["missing: infra/docker/.env.example"])
        return
    infra_need = ("DATABASE_URL", "REDIS_URL", "SECRET_KEY", "CRON_SECRET", "APP_ENV")
    missing_infra = [k for k in infra_need if k not in infra_text]
    front_text = read_text(root, "frontend/.env.example")
    if front_text is None:
        rep.add("FAIL", "env example present", ["missing: frontend/.env.example"])
        return
    missing_front = [k for k in ("VITE_API_BASE_URL", "APP_ENV") if k not in front_text]
    missing = (["infra/docker/.env.example: " + ", ".join(missing_infra)]
               if missing_infra else []) + \
              (["frontend/.env.example: " + ", ".join(missing_front)]
               if missing_front else [])
    if missing:
        rep.add("FAIL", "env example keys", ["missing keys: " + "; ".join(missing)])
    else:
        rep.add("PASS", "env example keys present",
                ["infra/docker/.env.example: " + ", ".join(infra_need),
                 "frontend/.env.example: VITE_API_BASE_URL, APP_ENV",
                 "AI placeholders: " + ", ".join(k for k in
                  ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY")
                  if k in infra_text)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OneMarket deploy preflight (stdlib-only).")
    parser.add_argument("--root", default=None, help="repo root (default: parent of scripts/)")
    args = parser.parse_args(argv)
    try:
        root = repo_root(args.root)
    except Exception as exc:
        print("FAIL: cannot resolve repo root (%s: %s)" % (type(exc).__name__, exc))
        return 2
    if not root.is_dir():
        print("FAIL: repo root not found: %s (pass --root <path>)" % root)
        return 2
    if not (root / "backend").is_dir() or not (root / "frontend").is_dir():
        print("FAIL: expected backend/ and frontend/ under root %s" % root)
        return 2

    rep = Report()
    check_ci_yaml(root, rep)
    check_gitignore(root, rep)
    check_deploy_doc(root, rep)
    check_vercel_json(root, rep)
    check_api_entry(root, rep)
    check_migration_tables(root, rep)
    check_frontend_dist(root, rep)
    check_env_example(root, rep)

    for status, title, evidence in rep.rows:
        print("[%s] %s" % (status, title))
        for line in evidence:
            print("        - %s" % line)
    counts = rep.counts()
    print("summary: %d PASS, %d SKIP, %d FAIL" % (counts["PASS"], counts["SKIP"], counts["FAIL"]))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
