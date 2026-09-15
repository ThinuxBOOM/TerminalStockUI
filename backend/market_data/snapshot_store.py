"""Market snapshots: compressed OHLCV payloads + graceful-degrade persistence.

A *market snapshot* freezes one ``get_bars`` response (symbol/timeframe) as a
compressed binary payload so history can be replayed point-in-time (forecast
scoring, calibration audits) without keeping every raw bar row forever.

Table concept (DDL draft: ``infra/migrations/0006_snapshots.sql``)::

    market_snapshots(
      snapshot_id UUID PK, symbol TEXT, instrument_id UUID NULL (lineage),
      exchange_mic TEXT, ts TIMESTAMPTZ (capture time), timeframe TEXT,
      encoding TEXT, payload BYTEA (compressed), n_bars INT,
      raw_bytes INT, compressed_bytes INT, source TEXT, quality_grade TEXT,
      provenance JSONB, user_id TEXT NULL (future per-user hook), created_at)
    (Union ORM also keeps size_raw/size_stored mirrors + tier stub; writers
    populate the mirrors, see save_snapshot.)

Compression (``compress_bars`` / ``decompress_bars``):
  * ``gzip+json`` — gzip of the canonical JSON bar list (baseline).
  * ``delta-q100+gzip`` — prices quantized to cents (scale 100), stored as
    base ints + integer deltas, volumes as base + deltas, timestamps verbatim,
    then gzip. Round-trips within ``EPSILON = 0.01`` on OHLC, exact on
    volume/ts. ``compress_bars`` builds both and keeps the smallest, so the
    ``encoding`` column always names the winner.

Graceful degrade: persistence helpers accept ``db=None`` or a DB whose table
does not exist yet (Agent 7 owns migrations). Missing tables fall back to a
bounded in-memory store instead of raising, so snapshot/scoring workers run
before ``0006`` is applied.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: Price quantization scale (cents). Max round-trip error per price is half a
#: cent; the public epsilon bound below keeps a safety margin.
PRICE_SCALE = 100
#: Round-trip tolerance for OHLC values (quantization + float repr).
EPSILON = 0.01

ENCODING_RAW_GZIP = "gzip+json"
ENCODING_DELTA_Q = "delta-q100+gzip"
#: Optional stronger/lighter frames. ``zstd`` needs the optional
#: ``zstandard`` package (never a hard dep); ``json+gzip`` is the stdlib
#: zlib frame (slightly different header than gzip). Both are already in the
#: DB CHECK (models MarketSnapshot) so no migration is needed.
ENCODING_ZSTD = "zstd"
ENCODING_ZLIB = "json+gzip"
ENCODINGS = (ENCODING_DELTA_Q, ENCODING_RAW_GZIP, ENCODING_ZLIB, ENCODING_ZSTD)

#: Bounded in-memory fallback when the DB/table is unavailable.
_MEMORY_MAX = 200
_MEMORY: dict[str, dict] = {}

#: Grades accepted by the union CHECK (models MarketSnapshot). Anything else
#: (e.g. ``F``/``U`` from outage grading) persists as ``D``; the true grade
#: always stays verbatim in ``provenance``.
_PERSIST_GRADES = ("A", "B", "C", "D")


def _persist_grade(grade: str | None) -> str:
    text = (grade or "C").strip().upper()
    return text if text in _PERSIST_GRADES else "D"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_rows(bars: list[dict]) -> list[dict]:
    """Normalize bar dicts to ``{ts, open, high, low, close, volume}``.

    Raises ``ValueError`` on empty input or incomplete rows (None OHLC):
    callers filter incomplete bars first (mirrors ForecastService._load).
    """
    if not bars:
        raise ValueError("bars must be a non-empty list")
    out: list[dict] = []
    for i, row in enumerate(bars):
        if not isinstance(row, dict):
            raise ValueError(f"bar {i} is not a dict")
        try:
            ts = row.get("ts")
            o, h, lo, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
        except AttributeError:
            raise ValueError(f"bar {i} is malformed")
        if o is None or h is None or lo is None or c is None:
            raise ValueError(f"bar {i} has incomplete OHLC (filter first)")
        try:
            vol = row.get("volume")
            volume = int(vol) if vol is not None else 0
        except (TypeError, ValueError):
            volume = 0
        out.append({
            "ts": str(ts),
            "open": float(o),
            "high": float(h),
            "low": float(lo),
            "close": float(c),
            "volume": volume,
        })
    return out


def _raw_json_bytes(rows: list[dict]) -> bytes:
    return json.dumps(rows, separators=(",", ":"), default=str).encode("utf-8")


def _encode_delta_q(rows: list[dict]) -> bytes:
    """Quantize OHLC to cents, delta-encode, return the *uncompressed* JSON."""
    q = [[int(round(r[k] * PRICE_SCALE)) for k in ("open", "high", "low", "close")]
         for r in rows]
    vols = [int(r["volume"]) for r in rows]
    doc = {
        "v": 1,
        "scale": PRICE_SCALE,
        "t": [r["ts"] for r in rows],
        "base": q[0],
        "base_vol": vols[0],
        "d": [[b - a for a, b in zip(q[i], q[i + 1])] for i in range(len(q) - 1)],
        "dv": [b - a for a, b in zip(vols, vols[1:])],
    }
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def _decode_delta_q(payload: bytes) -> list[dict]:
    try:
        doc = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"corrupt delta-q payload: {exc}") from exc
    try:
        scale = float(doc["scale"])
        ts_list = list(doc["t"])
        base = [int(x) for x in doc["base"]]
        deltas = [[int(x) for x in row] for row in doc["d"]]
        base_vol = int(doc["base_vol"])
        dvols = [int(x) for x in doc["dv"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed delta-q document: {exc}") from exc
    if len(base) != 4:
        raise ValueError("malformed delta-q document: base must have 4 prices")
    if not (len(ts_list) == len(deltas) + 1 == len(dvols) + 1):
        raise ValueError("malformed delta-q document: length mismatch")
    rows: list[dict] = []
    cur = list(base)
    vol = base_vol
    keys = ("open", "high", "low", "close")
    rows.append({"ts": str(ts_list[0]), **{k: cur[j] / scale for j, k in enumerate(keys)},
                 "volume": vol})
    for i, (drow, dv, ts) in enumerate(zip(deltas, dvols, ts_list[1:])):
        if len(drow) != 4:
            raise ValueError(f"malformed delta-q document: delta row {i} width")
        cur = [a + b for a, b in zip(cur, drow)]
        vol += dv
        rows.append({"ts": str(ts), **{k: cur[j] / scale for j, k in enumerate(keys)},
                     "volume": vol})
    return rows


def _compress_zstd(raw: bytes) -> bytes | None:
    """zstd frame or None when the optional lib is missing (fallback to gzip)."""
    try:
        import zstandard as _zstd  # type: ignore[import-not-found]
    except Exception:
        try:
            import zstd as _zstd  # type: ignore[import-not-found,no-redef]
        except Exception:
            return None
    try:
        return _zstd.ZstdCompressor(level=3).compress(raw)
    except Exception:
        return None


def _decompress_zstd(blob: bytes) -> bytes:
    try:
        import zstandard as _zstd  # type: ignore[import-not-found]
    except Exception:
        try:
            import zstd as _zstd  # type: ignore[import-not-found,no-redef]
        except Exception as exc:
            raise ValueError(f"zstd payload but zstandard lib missing: {exc}") from exc
    try:
        return _zstd.ZstdDecompressor().decompress(bytes(blob))
    except Exception as exc:
        raise ValueError(f"corrupt zstd frame: {exc}") from exc


def compress_bars(bars: list[dict]) -> dict:
    """Compress ``bars``; return ``{encoding, blob, n_bars, raw_bytes, ...}``.

    Builds every known encoding and keeps the smallest blob. ``raw_bytes``
    is the canonical JSON size so ``ratio = compressed/raw`` and
    ``size_reduction_pct`` are directly comparable across encodings.
    Candidates: delta-q+gzip, gzip+json, zlib (json+gzip), zstd
    (optional lib, skipped when missing). Never returns None; raises
    ``ValueError`` on bad input.
    """
    rows = _canonical_rows(bars)
    raw = _raw_json_bytes(rows)
    delta_raw = _encode_delta_q(rows)
    candidates: dict[str, bytes] = {
        ENCODING_RAW_GZIP: gzip.compress(raw, compresslevel=9),
        ENCODING_DELTA_Q: gzip.compress(delta_raw, compresslevel=9),
        ENCODING_ZLIB: zlib.compress(raw, level=9),
    }
    zstd_delta = _compress_zstd(delta_raw)
    if zstd_delta is not None:
        candidates[ENCODING_ZSTD] = zstd_delta
    encoding = min(candidates, key=lambda k: len(candidates[k]))
    blob = candidates[encoding]
    raw_n, comp_n = len(raw), len(blob)
    return {
        "encoding": encoding,
        "blob": blob,
        "n_bars": len(rows),
        "raw_bytes": raw_n,
        "compressed_bytes": comp_n,
        "ratio": (comp_n / raw_n) if raw_n else 1.0,
        "size_reduction_pct": round(100.0 * (1.0 - comp_n / raw_n), 2) if raw_n else 0.0,
    }


def decompress_bars(blob: bytes, encoding: str) -> list[dict]:
    """Inverse of :func:`compress_bars`. Raises ``ValueError`` on misuse."""
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise ValueError("blob must be non-empty bytes")
    if encoding == ENCODING_RAW_GZIP:
        try:
            rows = json.loads(gzip.decompress(bytes(blob)).decode("utf-8"))
        except (ValueError, EOFError, OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"corrupt gzip+json payload: {exc}") from exc
        if not isinstance(rows, list) or not rows:
            raise ValueError("gzip+json payload must be a non-empty list")
        return _canonical_rows(rows)
    if encoding == ENCODING_DELTA_Q:
        try:
            raw = gzip.decompress(bytes(blob))
        except (ValueError, EOFError, OSError) as exc:
            raise ValueError(f"corrupt delta-q gzip frame: {exc}") from exc
        return _decode_delta_q(raw)
    if encoding == ENCODING_ZLIB:
        try:
            rows = json.loads(zlib.decompress(bytes(blob)).decode("utf-8"))
        except (ValueError, EOFError, OSError, UnicodeDecodeError, zlib.error) as exc:
            raise ValueError(f"corrupt json+gzip payload: {exc}") from exc
        if not isinstance(rows, list) or not rows:
            raise ValueError("json+gzip payload must be a non-empty list")
        return _canonical_rows(rows)
    if encoding == ENCODING_ZSTD:
        # zstd frame holds the delta-q JSON document.
        return _decode_delta_q(_decompress_zstd(bytes(blob)))
    # Magic sniff for mislabeled frames (forward-compat, still strict).
    try:
        head = bytes(blob[:4])
    except Exception:
        head = b""
    if head.startswith(b"\x28\xb5\x2f\xfd"):
        raise ValueError(f"zstd frame labelled {encoding!r}; want one of {ENCODINGS}")
    raise ValueError(f"unknown snapshot encoding {encoding!r}; want one of {ENCODINGS}")


def bars_equal_within_epsilon(first: list[dict], second: list[dict],
                              epsilon: float = EPSILON) -> bool:
    """True when two bar lists match (ts/volume exact, OHLC within epsilon)."""
    if len(first) != len(second):
        return False
    for a, b in zip(first, second):
        if str(a.get("ts")) != str(b.get("ts")):
            return False
        if int(a.get("volume") or 0) != int(b.get("volume") or 0):
            return False
        for key in ("open", "high", "low", "close"):
            try:
                if abs(float(a[key]) - float(b[key])) > epsilon:
                    return False
            except (TypeError, ValueError, KeyError):
                return False
    return True


# --- persistence (graceful degrade) -----------------------------------------


def _memory_key(symbol: str, timeframe: str, ts_iso: str) -> str:
    return f"{(symbol or '').strip().upper()}|{(timeframe or '1d')}|{ts_iso}"


def _remember(record: dict) -> None:
    try:
        if len(_MEMORY) >= _MEMORY_MAX:
            _MEMORY.pop(next(iter(_MEMORY)))
        _MEMORY[_memory_key(record.get("symbol", ""),
                            record.get("timeframe", "1d"),
                            str(record.get("ts") or record.get("created_at") or ""))] = record
    except Exception:
        pass


def clear_memory() -> None:  # test hook
    try:
        _MEMORY.clear()
    except Exception:
        pass


def save_snapshot(db: Any, *, symbol: str, timeframe: str = "1d",
                  bars: list[dict] | None = None,
                  compressed: dict | None = None,
                  source: str = "yfinance", quality_grade: str = "C",
                  provenance: dict | None = None,
                  instrument_id: Any = None, exchange_mic: str | None = None,
                  user_id: str | None = None,
                  ts: datetime | None = None) -> dict:
    """Compress (unless ``compressed`` is given) + persist one snapshot.

    Returns ``{ok, persisted, snapshot_id, encoding, n_bars, raw_bytes,
    compressed_bytes, ratio, size_reduction_pct, reason}``. ``persisted``
    is False (with ``reason``) when ``db`` is None or the table is missing —
    the payload is still compressed and kept in the in-memory fallback, so
    workers stay green before migration 0006 is applied. Never raises.
    """
    stamp = ts or _utcnow()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    try:
        if compressed is None:
            compressed = compress_bars(bars or [])
        blob, encoding = compressed["blob"], compressed["encoding"]
        n_bars = int(compressed["n_bars"])
    except ValueError as exc:
        return {"ok": False, "persisted": False, "snapshot_id": None,
                "reason": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # defensive: never break a cron tick
        return {"ok": False, "persisted": False, "snapshot_id": None,
                "reason": type(exc).__name__}
    record = {
        "symbol": (symbol or "").strip().upper(),
        "timeframe": timeframe or "1d",
        "ts": stamp,
        "encoding": encoding,
        "n_bars": n_bars,
        "raw_bytes": int(compressed.get("raw_bytes") or 0),
        "compressed_bytes": int(compressed.get("compressed_bytes") or 0),
        "ratio": float(compressed.get("ratio") or 1.0),
        "size_reduction_pct": float(compressed.get("size_reduction_pct") or 0.0),
        "source": source or "yfinance",
        "quality_grade": quality_grade or "C",
        "created_at": stamp,
    }
    if db is None:
        _remember({**record, "blob": bytes(blob), "provenance": dict(provenance or {})})
        return {**record, "ok": True, "persisted": False, "snapshot_id": None,
                "reason": "no db: in-memory fallback"}
    try:
        from backend.db.models import MarketSnapshot

        row = MarketSnapshot(
            symbol=record["symbol"],
            instrument_id=instrument_id,
            exchange_mic=exchange_mic,
            ts=stamp,
            timeframe=record["timeframe"],
            encoding=encoding,
            payload=bytes(blob),
            n_bars=n_bars,
            raw_bytes=record["raw_bytes"],
            compressed_bytes=record["compressed_bytes"],
            # Union mirrors for instrument-keyed readers (same numbers).
            size_raw=record["raw_bytes"],
            size_stored=record["compressed_bytes"],
            source=record["source"],
            quality_grade=_persist_grade(quality_grade),
            provenance=dict(provenance or {}),
            user_id=user_id,  # nullable hook for future per-user tracking
        )
        db.add(row)
        db.commit()
        try:
            db.refresh(row)
            snapshot_id = str(row.snapshot_id)
        except Exception:
            snapshot_id = None
        return {**record, "ok": True, "persisted": True,
                "snapshot_id": snapshot_id, "reason": None}
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        _remember({**record, "blob": bytes(blob), "provenance": dict(provenance or {})})
        return {**record, "ok": True, "persisted": False, "snapshot_id": None,
                "reason": f"{type(exc).__name__}: table missing or write failed"}


def load_latest_snapshot(db: Any, symbol: str,
                         timeframe: str = "1d") -> dict | None:
    """Latest snapshot for (symbol, timeframe) with decompressed ``bars``.

    Reads the DB row when the table exists, else the in-memory fallback.
    Returns None when nothing is stored. Never raises.
    """
    sym = (symbol or "").strip().upper()
    if db is not None:
        try:
            from backend.db.models import MarketSnapshot

            row = (
                db.query(MarketSnapshot)
                .filter(MarketSnapshot.symbol == sym,
                        MarketSnapshot.timeframe == (timeframe or "1d"))
                .order_by(MarketSnapshot.ts.desc())
                .first()
            )
            if row is not None:
                bars = decompress_bars(bytes(row.payload), str(row.encoding))
                return {"symbol": sym, "timeframe": row.timeframe, "ts": row.ts,
                        "encoding": row.encoding, "n_bars": row.n_bars,
                        "bars": bars, "source": row.source,
                        "provenance": dict(row.provenance or {})}
        except Exception:
            pass
    try:
        cands = [r for r in _MEMORY.values()
                 if str(r.get("symbol")).upper() == sym
                 and str(r.get("timeframe")) == (timeframe or "1d")]
        if not cands:
            return None
        rec = sorted(cands, key=lambda r: str(r.get("ts")))[-1]
        return {**rec, "bars": decompress_bars(bytes(rec["blob"]), str(rec["encoding"]))}
    except Exception:
        return None


__all__ = [
    "ENCODINGS",
    "ENCODING_DELTA_Q",
    "ENCODING_RAW_GZIP",
    "ENCODING_ZLIB",
    "ENCODING_ZSTD",
    "EPSILON",
    "PRICE_SCALE",
    "bars_equal_within_epsilon",
    "clear_memory",
    "compress_bars",
    "decompress_bars",
    "load_latest_snapshot",
    "save_snapshot",
]
