"""
SQLAlchemy database models for Silicon to Software (S2S).
Supports PostgreSQL (production) and SQLite (development/demo).

Session strategy:
- Sync  (get_session):       used by FastAPI route handlers and Streamlit
- Async (get_async_session): used by PipelineService / ChatService background tasks
  so they never block the FastAPI event loop.

For SQLite the async URL is derived by inserting "+aiosqlite" into the scheme.
For Postgres the async URL uses "+asyncpg".
"""

from datetime import datetime
import logging
from typing import AsyncGenerator

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, JSON, Float,
    ForeignKey, create_engine, event,
)
from sqlalchemy.orm import (
    DeclarativeBase, relationship, sessionmaker, Session,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker,
)

from config import settings


def _async_url(sync_url: str) -> str:
    """Derive an async-compatible database URL from the sync one."""
    if sync_url.startswith("sqlite:///"):
        return sync_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


class Base(DeclarativeBase):
    pass


class ProjectDB(Base):
    """Project tracking table."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    design_type = Column(String(50), default="rf")
    # Wizard-selected design scope — drives which phases are applicable.
    # One of: "full", "front-end", "downconversion", "dsp".
    # Persisted here (instead of relying on the browser's localStorage) so
    # that GET /status can tell the frontend authoritatively which phases
    # apply and the /phases/{id}/execute endpoint can reject out-of-scope
    # runs with a 409. Added via migration 003.
    design_scope = Column(String(32), default="full", nullable=False)
    # Project direction: "receiver" (default) or "transmitter". Drives which
    # Round-1 question set the P1 agent shows (TX supplement prepended when
    # transmitter), which architecture catalogue the wizard offers, and
    # which branch of tools/rf_cascade.py is used for cascade analysis.
    # Added via migration 004.
    project_type = Column(String(32), default="receiver", nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Current phase status
    current_phase = Column(String(10), default="P1")
    phase_statuses = Column(JSON, default=dict)

    # Conversation history (Phase 1)
    conversation_history = Column(JSON, default=list)

    # Design parameters extracted
    design_parameters = Column(JSON, default=dict)

    # Output directory path
    output_dir = Column(String(500), default="")

    # ── A1.3 / A1.1 — Requirements lock columns (added via migration 001) ────
    # When P1 re-completes and new requirements are frozen, `requirements_hash`
    # is updated. Downstream phases are "stale" if their
    # phase_statuses[phase_id]["requirements_hash_at_completion"] does not match
    # the current value in this column.
    requirements_hash = Column(Text, nullable=True)
    requirements_frozen_at = Column(DateTime, nullable=True)
    requirements_locked_json = Column(Text, nullable=True)

    # Relationships
    phase_outputs = relationship("PhaseOutputDB", back_populates="project", cascade="all, delete-orphan")
    components = relationship("ComponentCacheDB", back_populates="project", cascade="all, delete-orphan")


class PhaseOutputDB(Base):
    """Output files and data from each phase."""
    __tablename__ = "phase_outputs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    phase_number = Column(String(10), nullable=False)
    phase_name = Column(String(100), nullable=False)

    # Output
    output_type = Column(String(50))  # markdown, json, xlsx, mermaid
    file_path = Column(String(500))
    content = Column(Text, default="")
    extra_data = Column(JSON, default=dict)

    # Timing
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_seconds = Column(Float)

    # LLM usage
    model_used = Column(String(100))
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)

    # Status
    status = Column(String(20), default="pending")
    error_message = Column(Text)

    # Relationship
    project = relationship("ProjectDB", back_populates="phase_outputs")


class ComponentCacheDB(Base):
    """Cached component data from scraping."""
    __tablename__ = "component_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    part_number = Column(String(100), nullable=False, index=True)
    manufacturer = Column(String(100))
    description = Column(Text)
    category = Column(String(50))
    key_specs = Column(JSON, default=dict)
    datasheet_url = Column(String(500))
    datasheet_text = Column(Text, default="")
    compliance = Column(JSON, default=list)
    lifecycle_status = Column(String(20), default="active")
    estimated_cost_usd = Column(Float)
    source = Column(String(50))  # digikey, mouser, manual, synthetic
    cached_at = Column(DateTime, default=datetime.now)

    project = relationship("ProjectDB", back_populates="components")


class ComplianceRecordDB(Base):
    """Compliance validation results."""
    __tablename__ = "compliance_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    part_number = Column(String(100))
    standard = Column(String(50))  # RoHS, REACH, FCC, CE, etc.
    status = Column(String(20))  # pass, fail, review, unknown
    details = Column(Text, default="")
    checked_at = Column(DateTime, default=datetime.now)


class PipelineRunDB(Base):
    """Reproducibility harness — one row per phase execution."""
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(100), nullable=False, index=True)
    phase_id = Column(String(20), nullable=False, index=True)
    started_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")

    # Snapshot of the requirements lock at the instant this run began —
    # used by A2.1 stale-phase detection to compare against the current hash.
    requirements_hash_at_run = Column(Text, nullable=True)

    model = Column(String(100), nullable=True)
    model_version = Column(String(100), nullable=True)
    total_tokens_in = Column(Integer, default=0)
    total_tokens_out = Column(Integer, default=0)
    wall_clock_ms = Column(Integer, nullable=True)


class LlmCallDB(Base):
    """Per-call LLM trace — every call in a pipeline run writes one row.

    Feeds the baseline eval and the reproducibility demo (D2.2). The hashes are
    SHA256(prompt) and SHA256(response) — for exact-match reproduction checking
    without storing the raw payloads.
    """
    __tablename__ = "llm_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    timestamp = Column(DateTime, default=datetime.now)
    model = Column(String(100), nullable=False)
    model_version = Column(String(100), nullable=True)
    temperature = Column(Float, nullable=True)
    top_p = Column(Float, nullable=True)
    prompt_sha256 = Column(String(64), nullable=True)
    response_sha256 = Column(String(64), nullable=True)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    tool_calls_json = Column(Text, nullable=True)


# --- Sync Engine & Session (used by Streamlit / simple route reads) ---

_engine = None
_SessionLocal = None

_db_log = logging.getLogger(__name__)


def _resolve_sqlite_url(url: str) -> str:
    """Ensure the SQLite DB is on a filesystem that supports proper locking.

    Mounted/network filesystems (VirtioFS, CIFS, NFS) can leave stale WAL
    files that make the DB unopenable.  If we detect that, copy the DB to
    /tmp and use it from there.
    """
    import os
    import shutil
    import tempfile
    import sqlite3

    # Extract file path from sqlite:///path
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    db_path = url[len(prefix):]
    if db_path.startswith("./"):
        db_path = os.path.join(os.getcwd(), db_path[2:])

    # Quick health check — can we open the DB at all?
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("SELECT 1")
        conn.close()
        return url  # Works fine, use original path
    except Exception as e:
        _db_log.warning("SQLite at %s failed (%s) — relocating to /tmp", db_path, e)

    # Relocate: copy the main .db (skip .wal/.shm) to /tmp
    tmp_db = os.path.join(tempfile.gettempdir(), os.path.basename(db_path))
    if os.path.exists(db_path):
        shutil.copy2(db_path, tmp_db)
        _db_log.info("Copied DB from %s to %s", db_path, tmp_db)
    # Remove any stale WAL/SHM in /tmp too
    for ext in ("-wal", "-shm"):
        p = tmp_db + ext
        if os.path.exists(p):
            os.remove(p)

    # Verify the copy works
    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("SELECT 1")
    conn.close()
    _db_log.info("SQLite relocated DB working at %s", tmp_db)
    return f"sqlite:///{tmp_db}"


_resolved_db_url: str | None = None   # cached after first resolution


def get_engine():
    global _engine, _resolved_db_url
    if _engine is None:
        db_url = settings.database_url
        is_sqlite = db_url.startswith("sqlite")
        if is_sqlite:
            db_url = _resolve_sqlite_url(db_url)
        _resolved_db_url = db_url
        _engine = create_engine(
            db_url,
            echo=settings.debug,
            future=True,
            pool_pre_ping=True,
            # SQLite: allow multiple threads to share the same connection
            connect_args={"check_same_thread": False} if is_sqlite else {},
        )
        # SQLite pragmas for performance
        if is_sqlite:
            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        Base.metadata.create_all(_engine)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


# --- Async Engine & Session (used by PipelineService / ChatService) ---
# Background tasks run inside the FastAPI async event loop — using the sync
# session would block it.  AsyncSession + aiosqlite keeps everything non-blocking.

_async_engine = None
_AsyncSessionLocal: async_sessionmaker | None = None


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        # Use the same resolved URL as the sync engine (avoids WAL issues)
        if _resolved_db_url:
            sync_url = _resolved_db_url
        elif settings.database_url.startswith("sqlite"):
            sync_url = _resolve_sqlite_url(settings.database_url)
        else:
            sync_url = settings.database_url
        url = _async_url(sync_url)
        _async_engine = create_async_engine(
            url,
            echo=settings.debug,
        )
    return _async_engine


def get_async_session_factory() -> async_sessionmaker:
    """Return (and lazily create) the async session factory."""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker(
            bind=get_async_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _AsyncSessionLocal


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager / FastAPI dependency that yields an AsyncSession.

    Usage in services:
        async with get_async_session_factory()() as session:
            ...
    """
    factory = get_async_session_factory()
    async with factory() as session:
        yield session
