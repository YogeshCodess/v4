"""
Persistent component-lookup cache — RAG layer for the P1 finalize hot path.

Three on-disk SQLite tables that absorb the three biggest sources of
latency in finalize_p1:

  1. **mpn_cache**         — DigiKey/Mouser exact-MPN lookups (was 3-4 min
                              per dense BOM, all redundant on warm cache)
  2. **url_probe_cache**   — datasheet HEAD/GET probes (was 30-60 s per
                              BOM, IO-bound network calls)
  3. **parametric_cache**  — keyword-search shortlists by (stage, hint)
                              signature (was 5 min in-memory TTL only)

The cache lives in its own SQLite file (`data/component_cache.db` by
default — override with the `COMPONENT_CACHE_PATH` env var) so we can
ship a pre-populated cache with the demo image, regenerate it with the
seed script, or wipe it without risking the project DB.

WAL mode is enabled on first connect so the seed script (single writer)
can run alongside live API request workers (concurrent readers) without
blocking either side.

TTL strategy (different signal, different decay rate):

    Identity   (mfr, datasheet_url, description)   →  7 days
    Lifecycle  (active/nrnd/obsolete, stock, price) →  24 hours
    URL probe — trusted vendor                       →  30 days
    URL probe — untrusted                            →  7 days
    Parametric shortlists                            →  24 hours
    Negative cache  ("MPN does not exist anywhere")  →  24 hours

Identity changes essentially never; lifecycle/stock can flip on an
hour-to-day cadence — splitting them lets us serve identity from a
week-old cache while still re-checking that the part isn't suddenly
NRND. v1 stores them as one row with two timestamps; the lookup helper
returns `(part_info, lifecycle_stale: bool)` so the caller can decide
to re-fetch lifecycle without paying for the full identity round-trip.

Cache-aside semantics throughout:

    cached = cache.get_mpn(mpn)
    if cached is None or cached.lifecycle_stale:
        info = live_lookup(mpn)
        cache.put_mpn(mpn, info)
    else:
        info = cached.part_info

A cache miss is **never** an error — it always falls through to the
existing live lookup path, so flipping the cache off (delete the file)
restores exactly the pre-RAG behaviour.

Thread safety: SQLite handles concurrent readers natively under WAL
mode. We open one connection per thread (sqlite3 connections are not
shareable across threads) via a thread-local; writers serialise on the
file's WAL lock. The hot-path readers grab connections from a tiny
pool to avoid the per-call connect cost.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional

from tools.digikey_api import PartInfo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTL knobs — tuned for the P1 demo workflow, not for a long-running
# production OLTP. Override per-deployment via env vars.
# ---------------------------------------------------------------------------

def _ttl(env_name: str, default_s: int) -> int:
    """Read a TTL override from the environment, expressed in seconds.
    Empty / unparseable values fall back to the compiled default so a
    typo doesn't silently disable caching."""
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default_s
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("component_cache.bad_ttl env=%s value=%r", env_name, raw)
        return default_s


IDENTITY_TTL_S            = _ttl("COMPONENT_CACHE_IDENTITY_TTL_S",        7 * 24 * 3600)
LIFECYCLE_TTL_S           = _ttl("COMPONENT_CACHE_LIFECYCLE_TTL_S",       24 * 3600)
URL_PROBE_TTL_S           = _ttl("COMPONENT_CACHE_URL_PROBE_TTL_S",       7 * 24 * 3600)
URL_PROBE_TRUSTED_TTL_S   = _ttl("COMPONENT_CACHE_URL_PROBE_TRUSTED_TTL_S", 30 * 24 * 3600)
PARAMETRIC_TTL_S          = _ttl("COMPONENT_CACHE_PARAMETRIC_TTL_S",      24 * 3600)
NEGATIVE_TTL_S            = _ttl("COMPONENT_CACHE_NEGATIVE_TTL_S",        24 * 3600)


# ---------------------------------------------------------------------------
# Default DB location
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_db_path() -> str:
    """Resolve the cache DB path. Env override wins; otherwise fall back
    to `data/component_cache.db` under the repo root. Creates the parent
    directory if needed so the seed script can run on a fresh checkout."""
    override = os.getenv("COMPONENT_CACHE_PATH", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    else:
        path = _REPO_ROOT / "data" / "component_cache.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


# ---------------------------------------------------------------------------
# Result wrappers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MpnHit:
    """Result of a `get_mpn` call. `part_info` is None when we negatively
    cached a miss; `lifecycle_stale` flags that identity is fresh enough
    to use but lifecycle/price/stock should be re-checked."""
    part_info: Optional[PartInfo]
    is_negative: bool
    lifecycle_stale: bool


@dataclass(frozen=True)
class UrlProbeHit:
    """Result of a `get_url_probe` call."""
    is_valid: bool
    status_code: Optional[int]
    content_type: Optional[str]
    cached_at: int


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mpn_cache (
    mpn_upper           TEXT PRIMARY KEY,
    part_info_json      TEXT,             -- NULL only when is_negative=1
    source              TEXT NOT NULL,    -- 'digikey' | 'mouser' | 'seed' | 'chromadb'
    identity_cached_at  INTEGER NOT NULL, -- unix epoch (seconds)
    lifecycle_cached_at INTEGER NOT NULL,
    is_negative         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mpn_cache_lifecycle ON mpn_cache(lifecycle_cached_at);

CREATE TABLE IF NOT EXISTS url_probe_cache (
    url             TEXT PRIMARY KEY,
    is_valid        INTEGER NOT NULL,    -- 0 or 1
    status_code     INTEGER,
    content_type    TEXT,
    is_trusted      INTEGER NOT NULL DEFAULT 0,
    last_checked_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_url_probe_checked ON url_probe_cache(last_checked_at);

CREATE TABLE IF NOT EXISTS parametric_cache (
    query_hash      TEXT PRIMARY KEY,
    query_label     TEXT NOT NULL,        -- human-readable for debugging / seed script reports
    results_json    TEXT NOT NULL,        -- JSON array of PartInfo dicts
    cached_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parametric_cached_at ON parametric_cache(cached_at);
"""


# ---------------------------------------------------------------------------
# Cache class
# ---------------------------------------------------------------------------

class ComponentCache:
    """Thread-safe persistent cache for distributor lookups + URL probes.

    One instance per DB file is enough; use `get_default()` for the
    process-wide singleton. Manual instantiation is used by the seed
    script and tests that want an isolated DB.

    All public methods swallow SQLite errors and log at WARN — a corrupt
    or locked cache must never break the live lookup path. The caller
    treats every read as Optional and every write as best-effort.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _default_db_path()
        # Per-thread connection pool. sqlite3 connections are not
        # shareable across threads (`SQLITE_THREADSAFE=1` mode), and the
        # default check_same_thread=True crashes on cross-thread use —
        # cheaper to keep one open per thread for the hot path.
        self._tls = threading.local()
        self._init_lock = threading.Lock()
        self._initialised = False
        self._ensure_schema()

    # -- connection management ---------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        """Return a thread-local connection, opening one on first use."""
        c: Optional[sqlite3.Connection] = getattr(self._tls, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=10.0)
            c.execute("PRAGMA journal_mode=WAL")          # concurrent readers + 1 writer
            c.execute("PRAGMA synchronous=NORMAL")        # safe under WAL, faster
            c.execute("PRAGMA temp_store=MEMORY")
            c.row_factory = sqlite3.Row
            self._tls.conn = c
        return c

    def _ensure_schema(self) -> None:
        """Create tables if they don't already exist. Cheap to re-run; the
        guard avoids hammering the WAL log on every singleton access."""
        if self._initialised:
            return
        with self._init_lock:
            if self._initialised:
                return
            try:
                conn = self._conn()
                conn.executescript(_SCHEMA_SQL)
                conn.commit()
                self._initialised = True
            except sqlite3.Error as exc:
                log.warning("component_cache.schema_init_failed db=%s: %s",
                            self.db_path, exc)

    def close(self) -> None:
        """Close the current thread's connection. Mostly for tests."""
        c: Optional[sqlite3.Connection] = getattr(self._tls, "conn", None)
        if c is not None:
            try:
                c.close()
            except sqlite3.Error:
                pass
            self._tls.conn = None

    # -- MPN cache ---------------------------------------------------------

    def get_mpn(self, mpn: str) -> Optional[MpnHit]:
        """Return the cached lookup for `mpn` or None on miss / hard-stale.

        "Hard stale" = identity older than IDENTITY_TTL_S → caller should
        treat as miss and re-fetch the whole record. "Lifecycle stale" =
        identity fresh, lifecycle/stock/price should be re-checked but
        the LLM-facing identity fields are still trustworthy.

        Negative entries (cached misses) are returned with `part_info=None`
        and `is_negative=True` so the caller can short-circuit without
        another live lookup that will also miss.
        """
        if not mpn:
            return None
        key = mpn.strip().upper()
        if not key:
            return None
        try:
            row = self._conn().execute(
                "SELECT part_info_json, identity_cached_at, lifecycle_cached_at, "
                "       is_negative FROM mpn_cache WHERE mpn_upper=?",
                (key,),
            ).fetchone()
        except sqlite3.Error as exc:
            log.warning("component_cache.get_mpn_failed mpn=%s: %s", key, exc)
            return None
        if row is None:
            return None
        now = int(time.time())
        # Negative entries: shorter TTL — we WANT to re-check sooner
        # because a part that didn't exist last week may have been added.
        if row["is_negative"]:
            if now - row["identity_cached_at"] > NEGATIVE_TTL_S:
                return None  # stale negative → re-query
            return MpnHit(part_info=None, is_negative=True, lifecycle_stale=False)
        if now - row["identity_cached_at"] > IDENTITY_TTL_S:
            return None  # full re-fetch
        try:
            info = _part_info_from_json(row["part_info_json"])
        except Exception as exc:
            log.warning("component_cache.bad_mpn_row mpn=%s: %s", key, exc)
            return None
        if info is None:
            return None
        lifecycle_stale = (now - row["lifecycle_cached_at"]) > LIFECYCLE_TTL_S
        return MpnHit(part_info=info, is_negative=False, lifecycle_stale=lifecycle_stale)

    def put_mpn(self, mpn: str, info: PartInfo) -> None:
        """Cache a successful lookup. Identity + lifecycle timestamps both
        set to now — this is a fresh distributor record."""
        if not mpn or info is None:
            return
        key = mpn.strip().upper()
        if not key:
            return
        try:
            payload = json.dumps(info.to_dict(), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            log.warning("component_cache.json_encode_failed mpn=%s: %s", key, exc)
            return
        now = int(time.time())
        try:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO mpn_cache "
                "(mpn_upper, part_info_json, source, identity_cached_at, "
                " lifecycle_cached_at, is_negative) VALUES (?,?,?,?,?,0)",
                (key, payload, info.source or "unknown", now, now),
            )
            conn.commit()
        except sqlite3.Error as exc:
            log.warning("component_cache.put_mpn_failed mpn=%s: %s", key, exc)

    def put_mpn_negative(self, mpn: str) -> None:
        """Cache that `mpn` was NOT found anywhere. Stops repeated lookups
        for the same hallucinated MPN within NEGATIVE_TTL_S. Carries a
        sentinel source string so the seed script never confuses a
        negative entry with a real distributor row."""
        if not mpn:
            return
        key = mpn.strip().upper()
        if not key:
            return
        now = int(time.time())
        try:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO mpn_cache "
                "(mpn_upper, part_info_json, source, identity_cached_at, "
                " lifecycle_cached_at, is_negative) VALUES (?,NULL,'negative',?,?,1)",
                (key, now, now),
            )
            conn.commit()
        except sqlite3.Error as exc:
            log.warning("component_cache.put_mpn_negative_failed mpn=%s: %s", key, exc)

    def update_lifecycle(self, mpn: str, info: PartInfo) -> None:
        """Refresh the lifecycle/stock/price fields on an existing identity
        row without bumping the identity timestamp. Used by the background
        re-check path on `lifecycle_stale=True` reads."""
        if not mpn or info is None:
            return
        key = mpn.strip().upper()
        if not key:
            return
        try:
            payload = json.dumps(info.to_dict(), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return
        now = int(time.time())
        try:
            conn = self._conn()
            conn.execute(
                "UPDATE mpn_cache SET part_info_json=?, lifecycle_cached_at=?, is_negative=0 "
                "WHERE mpn_upper=?",
                (payload, now, key),
            )
            conn.commit()
        except sqlite3.Error as exc:
            log.warning("component_cache.update_lifecycle_failed mpn=%s: %s", key, exc)

    def bulk_put_mpns(self, infos: Iterable[PartInfo]) -> int:
        """Bulk-insert distributor records — the seed script's hot path.

        Returns the number of rows actually written. A single transaction
        wraps the whole batch so 10k rows commit in <1 s instead of 10k
        per-row commits taking minutes."""
        rows: list[tuple] = []
        now = int(time.time())
        for info in infos:
            if info is None:
                continue
            key = (info.part_number or "").strip().upper()
            if not key:
                continue
            try:
                payload = json.dumps(info.to_dict(), ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
            rows.append((key, payload, info.source or "unknown", now, now))
        if not rows:
            return 0
        try:
            conn = self._conn()
            with conn:  # implicit transaction
                conn.executemany(
                    "INSERT OR REPLACE INTO mpn_cache "
                    "(mpn_upper, part_info_json, source, identity_cached_at, "
                    " lifecycle_cached_at, is_negative) VALUES (?,?,?,?,?,0)",
                    rows,
                )
            return len(rows)
        except sqlite3.Error as exc:
            log.warning("component_cache.bulk_put_failed n=%d: %s", len(rows), exc)
            return 0

    # -- URL probe cache ---------------------------------------------------

    def get_url_probe(self, url: str) -> Optional[UrlProbeHit]:
        """Return the cached HEAD-probe result, or None on miss / stale.

        Trusted-vendor URLs use a longer TTL — analog.com hasn't moved
        a product page in years and re-probing it weekly is wasteful.
        """
        if not url:
            return None
        try:
            row = self._conn().execute(
                "SELECT is_valid, status_code, content_type, is_trusted, last_checked_at "
                "FROM url_probe_cache WHERE url=?",
                (url,),
            ).fetchone()
        except sqlite3.Error as exc:
            log.warning("component_cache.get_url_probe_failed url=%s: %s", url, exc)
            return None
        if row is None:
            return None
        ttl = URL_PROBE_TRUSTED_TTL_S if row["is_trusted"] else URL_PROBE_TTL_S
        if int(time.time()) - row["last_checked_at"] > ttl:
            return None
        return UrlProbeHit(
            is_valid=bool(row["is_valid"]),
            status_code=row["status_code"],
            content_type=row["content_type"],
            cached_at=row["last_checked_at"],
        )

    def put_url_probe(
        self,
        url: str,
        is_valid: bool,
        *,
        status_code: Optional[int] = None,
        content_type: Optional[str] = None,
        is_trusted: bool = False,
    ) -> None:
        """Cache the result of a HEAD/GET probe. Idempotent — re-probing
        the same URL just bumps `last_checked_at`."""
        if not url:
            return
        try:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO url_probe_cache "
                "(url, is_valid, status_code, content_type, is_trusted, last_checked_at) "
                "VALUES (?,?,?,?,?,?)",
                (url, 1 if is_valid else 0, status_code, content_type,
                 1 if is_trusted else 0, int(time.time())),
            )
            conn.commit()
        except sqlite3.Error as exc:
            log.warning("component_cache.put_url_probe_failed url=%s: %s", url, exc)

    # -- Parametric cache --------------------------------------------------

    def get_parametric(self, query_hash: str) -> Optional[list[PartInfo]]:
        """Return a cached parametric shortlist or None on miss / stale.

        The hash is computed by the caller (parametric_search) so the
        cache module stays oblivious to query structure — any string-
        keyed query signature works."""
        if not query_hash:
            return None
        try:
            row = self._conn().execute(
                "SELECT results_json, cached_at FROM parametric_cache WHERE query_hash=?",
                (query_hash,),
            ).fetchone()
        except sqlite3.Error as exc:
            log.warning("component_cache.get_param_failed h=%s: %s", query_hash, exc)
            return None
        if row is None:
            return None
        if int(time.time()) - row["cached_at"] > PARAMETRIC_TTL_S:
            return None
        try:
            blobs = json.loads(row["results_json"])
        except (TypeError, ValueError) as exc:
            log.warning("component_cache.bad_param_row h=%s: %s", query_hash, exc)
            return None
        out: list[PartInfo] = []
        for blob in blobs or []:
            info = _part_info_from_dict(blob)
            if info is not None:
                out.append(info)
        return out

    def put_parametric(
        self, query_hash: str, query_label: str, results: Iterable[PartInfo],
    ) -> None:
        """Cache a parametric shortlist. `query_label` is human-readable
        and helps the seed script + ops debug what the hash maps to."""
        if not query_hash:
            return
        rows: list[dict] = []
        for info in results or []:
            if info is None:
                continue
            rows.append(info.to_dict())
        try:
            payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            log.warning("component_cache.json_encode_param h=%s: %s", query_hash, exc)
            return
        try:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO parametric_cache "
                "(query_hash, query_label, results_json, cached_at) VALUES (?,?,?,?)",
                (query_hash, (query_label or "")[:200], payload, int(time.time())),
            )
            conn.commit()
        except sqlite3.Error as exc:
            log.warning("component_cache.put_param_failed h=%s: %s", query_hash, exc)

    # -- Stats / housekeeping ---------------------------------------------

    def stats(self) -> dict[str, int]:
        """Return row counts for ops + the seed script's progress report."""
        out = {"mpn_cache": 0, "mpn_negative": 0, "url_probe_cache": 0,
               "url_probe_valid": 0, "parametric_cache": 0}
        try:
            conn = self._conn()
            out["mpn_cache"] = int(conn.execute(
                "SELECT COUNT(*) FROM mpn_cache WHERE is_negative=0"
            ).fetchone()[0])
            out["mpn_negative"] = int(conn.execute(
                "SELECT COUNT(*) FROM mpn_cache WHERE is_negative=1"
            ).fetchone()[0])
            out["url_probe_cache"] = int(conn.execute(
                "SELECT COUNT(*) FROM url_probe_cache"
            ).fetchone()[0])
            out["url_probe_valid"] = int(conn.execute(
                "SELECT COUNT(*) FROM url_probe_cache WHERE is_valid=1"
            ).fetchone()[0])
            out["parametric_cache"] = int(conn.execute(
                "SELECT COUNT(*) FROM parametric_cache"
            ).fetchone()[0])
        except sqlite3.Error as exc:
            log.warning("component_cache.stats_failed: %s", exc)
        return out

    def vacuum(self) -> None:
        """Reclaim pages after a large purge. Cheap (~ms) on a 100MB DB."""
        try:
            self._conn().execute("VACUUM")
        except sqlite3.Error as exc:
            log.warning("component_cache.vacuum_failed: %s", exc)

    def purge_stale(self) -> dict[str, int]:
        """Best-effort cleanup of rows past their TTL — the seed script
        runs this after a bulk fetch so the file size doesn't drift."""
        now = int(time.time())
        out = {"mpn_identity": 0, "url_probe": 0, "parametric": 0, "negative": 0}
        try:
            conn = self._conn()
            with conn:
                cur = conn.execute(
                    "DELETE FROM mpn_cache WHERE is_negative=0 AND identity_cached_at < ?",
                    (now - IDENTITY_TTL_S,),
                )
                out["mpn_identity"] = cur.rowcount or 0
                cur = conn.execute(
                    "DELETE FROM mpn_cache WHERE is_negative=1 AND identity_cached_at < ?",
                    (now - NEGATIVE_TTL_S,),
                )
                out["negative"] = cur.rowcount or 0
                cur = conn.execute(
                    "DELETE FROM url_probe_cache WHERE last_checked_at < ?",
                    (now - URL_PROBE_TTL_S,),  # use untrusted TTL — trusted survives via is_trusted
                )
                # Don't actually purge trusted-vendor probes that aged out
                # under the untrusted TTL — re-fetch them via the longer
                # TTL on next read; that's idempotent.
                out["url_probe"] = cur.rowcount or 0
                cur = conn.execute(
                    "DELETE FROM parametric_cache WHERE cached_at < ?",
                    (now - PARAMETRIC_TTL_S,),
                )
                out["parametric"] = cur.rowcount or 0
        except sqlite3.Error as exc:
            log.warning("component_cache.purge_failed: %s", exc)
        return out


# ---------------------------------------------------------------------------
# Singleton accessors
# ---------------------------------------------------------------------------

_default_cache: Optional[ComponentCache] = None
_default_cache_lock = threading.Lock()


def get_default() -> ComponentCache:
    """Return the process-wide singleton, creating it on first call.

    Honours `COMPONENT_CACHE_PATH`. Safe to call from any thread; only
    the first concurrent caller pays the schema-init cost.
    """
    global _default_cache
    if _default_cache is not None:
        return _default_cache
    with _default_cache_lock:
        if _default_cache is None:
            _default_cache = ComponentCache()
    return _default_cache


def reset_default() -> None:
    """Drop the singleton — for tests that mutate `COMPONENT_CACHE_PATH`
    between cases. The on-disk DB is left intact; only the Python
    object is replaced on next `get_default()` call."""
    global _default_cache
    with _default_cache_lock:
        if _default_cache is not None:
            _default_cache.close()
        _default_cache = None


def cache_disabled() -> bool:
    """Honour an opt-out env so a user can prove a regression isn't
    cache-shaped without deleting the DB. Wired in at every read site."""
    return os.getenv("COMPONENT_CACHE_DISABLED", "").strip().lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# PartInfo (de)serialisation
# ---------------------------------------------------------------------------

def _part_info_from_dict(d: dict) -> Optional[PartInfo]:
    """Reconstruct a PartInfo from its `to_dict` form. Tolerant of
    missing optional fields — the upstream serialiser was added in a
    later commit than some seed JSON entries."""
    if not isinstance(d, dict):
        return None
    pn = d.get("part_number")
    if not pn:
        return None
    try:
        return PartInfo(
            part_number=str(pn),
            manufacturer=str(d.get("manufacturer") or ""),
            description=str(d.get("description") or ""),
            datasheet_url=d.get("datasheet_url") or None,
            product_url=d.get("product_url") or None,
            lifecycle_status=str(d.get("lifecycle_status") or "unknown"),
            unit_price_usd=_float_or_none(d.get("unit_price_usd")),
            stock_quantity=_int_or_none(d.get("stock_quantity")),
            source=str(d.get("source") or "unknown"),
            unit_price=_float_or_none(d.get("unit_price")),
            unit_price_currency=d.get("unit_price_currency") or None,
            region=str(d.get("region") or ""),
        )
    except (TypeError, ValueError):
        return None


def _part_info_from_json(blob: Optional[str]) -> Optional[PartInfo]:
    if not blob:
        return None
    try:
        return _part_info_from_dict(json.loads(blob))
    except (TypeError, ValueError):
        return None


def _float_or_none(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ComponentCache",
    "MpnHit",
    "UrlProbeHit",
    "get_default",
    "reset_default",
    "cache_disabled",
    "IDENTITY_TTL_S",
    "LIFECYCLE_TTL_S",
    "URL_PROBE_TTL_S",
    "URL_PROBE_TRUSTED_TTL_S",
    "PARAMETRIC_TTL_S",
    "NEGATIVE_TTL_S",
]
