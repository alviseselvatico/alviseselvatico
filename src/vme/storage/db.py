"""SQLite connection and migration runner."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from vme.domain.models import utc_now
from vme.logs import get_logger
from vme.storage.migrations import MIGRATIONS
from vme.storage.repositories import (
    BenchmarkRepository,
    CandidateRepository,
    ClaimRepository,
    EditorialRepository,
    LabelRepository,
    LlmCallRepository,
    MediaAssetRepository,
    RankingRepository,
    RenderRepository,
    ReviewEventRepository,
    RightsPolicyRepository,
    SourceRepository,
    TranscriptRepository,
)

log = get_logger("storage")
_NAME_RE = re.compile(r"[a-z0-9_]+")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating parent directories) with foreign keys enforced. ``:memory:`` works."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    return [int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations in order, each in its own transaction. Returns applied versions."""
    done = set(applied_versions(conn))
    expected = list(range(1, len(MIGRATIONS) + 1))
    if [m.version for m in MIGRATIONS] != expected:
        msg = "MIGRATIONS must be contiguous and 1-based"
        raise RuntimeError(msg)
    if any(v not in expected for v in done):
        msg = f"database has unknown migration versions applied: {sorted(done)}"
        raise RuntimeError(msg)
    applied: list[int] = []
    for m in MIGRATIONS:
        if m.version in done:
            continue
        if not _NAME_RE.fullmatch(m.name):
            msg = f"migration name must match {_NAME_RE.pattern!r}: {m.name!r}"
            raise RuntimeError(msg)
        # executescript() commits any pending transaction first, so the migration and its
        # bookkeeping row are wrapped in one explicit BEGIN/COMMIT inside the script.
        # Values are code-controlled (validated name, int version, ISO timestamp), not input.
        script = (
            "BEGIN;\n"
            f"{m.sql}\n"
            "INSERT INTO schema_migrations(version, name, applied_at) "
            f"VALUES ({int(m.version)}, '{m.name}', '{utc_now().isoformat()}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(m.version)
        log.info("migration_applied", extra={"version": m.version, "migration": m.name})
    return applied


class Store:
    """Unit-of-work over one connection with the Phase 0 repositories."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sources = SourceRepository(conn)
        self.policies = RightsPolicyRepository(conn)
        self.media = MediaAssetRepository(conn)
        self.transcripts = TranscriptRepository(conn)
        self.candidates = CandidateRepository(conn)
        self.llm_calls = LlmCallRepository(conn)
        self.ranking = RankingRepository(conn)
        self.editorial = EditorialRepository(conn)
        self.claims = ClaimRepository(conn)
        self.reviews = ReviewEventRepository(conn)
        self.renders = RenderRepository(conn)
        self.labels = LabelRepository(conn)
        self.benchmarks = BenchmarkRepository(conn)

    @classmethod
    def open(cls, db_path: Path | str) -> Store:
        conn = connect(db_path)
        migrate(conn)
        return cls(conn)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()
