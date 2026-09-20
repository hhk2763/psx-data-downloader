"""SQLite record of every fetch attempt: what was tried, and how it went.

The manifest is what makes a run resumable. It is advisory, not authoritative:
when it disagrees with the disk, the disk wins (see ``jobs.py`` and the
``reconcile`` command).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Literal, Sequence

Status = Literal["ok", "missing", "error"]

OK: Status = "ok"
MISSING: Status = "missing"
ERROR: Status = "error"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_log (
  dataset     TEXT NOT NULL,
  trade_date  TEXT NOT NULL,          -- YYYY-MM-DD
  ext         TEXT NOT NULL,
  status      TEXT NOT NULL,          -- ok | missing | error
  http_code   INTEGER,
  bytes       INTEGER,
  sha256      TEXT,
  attempts    INTEGER DEFAULT 1,
  error_msg   TEXT,
  fetched_at  TEXT NOT NULL,          -- ISO timestamp, UTC
  PRIMARY KEY (dataset, trade_date, ext)
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_status ON fetch_log (status);
CREATE INDEX IF NOT EXISTS idx_fetch_log_date ON fetch_log (trade_date);
"""

_UPSERT = """
INSERT INTO fetch_log
    (dataset, trade_date, ext, status, http_code, bytes, sha256, attempts, error_msg, fetched_at)
VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
ON CONFLICT (dataset, trade_date, ext) DO UPDATE SET
    status     = excluded.status,
    http_code  = excluded.http_code,
    bytes      = excluded.bytes,
    sha256     = excluded.sha256,
    attempts   = fetch_log.attempts + 1,
    error_msg  = excluded.error_msg,
    fetched_at = excluded.fetched_at
"""


def utc_now_iso() -> str:
    """Timestamps are always UTC, so logs from any machine sort together."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One row of ``fetch_log``."""

    dataset: str
    trade_date: date
    ext: str
    status: Status
    http_code: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    attempts: int = 1
    error_msg: str | None = None
    fetched_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> LogEntry:
        return cls(
            dataset=row["dataset"],
            trade_date=date.fromisoformat(row["trade_date"]),
            ext=row["ext"],
            status=row["status"],
            http_code=row["http_code"],
            bytes=row["bytes"],
            sha256=row["sha256"],
            attempts=row["attempts"],
            error_msg=row["error_msg"],
            fetched_at=row["fetched_at"],
        )


class Manifest:
    """Thin wrapper over one SQLite file. Use as a context manager."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    def __enter__(self) -> Manifest:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # --- writing -----------------------------------------------------------

    def record(self, entry: LogEntry) -> None:
        """Insert or update one row. A repeat attempt increments ``attempts``."""
        self.conn.execute(_UPSERT, self._params(entry))

    def bulk_record(self, entries: Iterable[LogEntry]) -> int:
        """Record many rows in a single transaction (used by ``reconcile``)."""
        rows = [self._params(e) for e in entries]
        if not rows:
            return 0
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(_UPSERT, rows)
        return len(rows)

    @staticmethod
    def _params(entry: LogEntry) -> tuple[object, ...]:
        return (
            entry.dataset,
            entry.trade_date.isoformat(),
            entry.ext,
            entry.status,
            entry.http_code,
            entry.bytes,
            entry.sha256,
            entry.error_msg,
            entry.fetched_at or utc_now_iso(),
        )

    # --- reading -----------------------------------------------------------

    def get(self, dataset: str, trade_date: date, ext: str) -> LogEntry | None:
        row = self.conn.execute(
            "SELECT * FROM fetch_log WHERE dataset = ? AND trade_date = ? AND ext = ?",
            (dataset, trade_date.isoformat(), ext),
        ).fetchone()
        return LogEntry.from_row(row) if row else None

    def iter_entries(self, dataset: str | None = None) -> Iterator[LogEntry]:
        sql = "SELECT * FROM fetch_log"
        params: tuple[object, ...] = ()
        if dataset is not None:
            sql += " WHERE dataset = ?"
            params = (dataset,)
        sql += " ORDER BY dataset, trade_date, ext"
        for row in self.conn.execute(sql, params):
            yield LogEntry.from_row(row)

    def count(self, status: Status | None = None) -> int:
        if status is None:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM fetch_log").fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM fetch_log WHERE status = ?", (status,)).fetchone()
        return int(row["n"])

    def counts_by_status(self, dataset: str | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM fetch_log"
        params: tuple[object, ...] = ()
        if dataset is not None:
            sql += " WHERE dataset = ?"
            params = (dataset,)
        sql += " GROUP BY status"
        return {row["status"]: int(row["n"]) for row in self.conn.execute(sql, params)}

    def coverage_rows(self) -> list[dict[str, object]]:
        """``(dataset, month, status, n)`` rows for the GUI's coverage heatmap."""
        sql = """
            SELECT dataset,
                   substr(trade_date, 1, 7) AS month,
                   status,
                   COUNT(*) AS n
            FROM fetch_log
            GROUP BY dataset, month, status
            ORDER BY dataset, month, status
        """
        return [dict(row) for row in self.conn.execute(sql)]

    # --- the resume decision ----------------------------------------------

    def should_fetch(
        self,
        dataset: str,
        trade_date: date,
        ext: str,
        *,
        force: bool = False,
        recheck_days: int = 3,
        today: date | None = None,
    ) -> bool:
        """Whether to spend a request on this file.

        ``ok``      -> skip, unless forced.
        ``missing`` -> skip, unless the date is recent enough that PSX might
                       still publish it late.
        ``error``   -> always retry.
        no row      -> fetch.
        """
        if force:
            return True

        entry = self.get(dataset, trade_date, ext)
        if entry is None:
            return True
        if entry.status == OK:
            return False
        if entry.status == MISSING:
            cutoff = (today or date.today()) - timedelta(days=recheck_days)
            return trade_date >= cutoff
        return True  # error, or an unrecognised status: try again

    # --- maintenance -------------------------------------------------------

    def delete_all(self) -> int:
        """Drop every row. ``reconcile`` uses this to rebuild from disk."""
        cursor = self.conn.execute("DELETE FROM fetch_log")
        return cursor.rowcount if cursor.rowcount > 0 else 0

    def delete(self, dataset: str, trade_date: date, ext: str) -> None:
        self.conn.execute(
            "DELETE FROM fetch_log WHERE dataset = ? AND trade_date = ? AND ext = ?",
            (dataset, trade_date.isoformat(), ext),
        )


def entry_for_stored_file(dataset: str, trade_date: date, ext: str, size: int, sha256: str) -> LogEntry:
    """Build an ``ok`` row for a file that is already on disk (``reconcile``)."""
    return LogEntry(
        dataset=dataset,
        trade_date=trade_date,
        ext=ext,
        status=OK,
        http_code=200,
        bytes=size,
        sha256=sha256,
        error_msg=None,
        fetched_at=utc_now_iso(),
    )
