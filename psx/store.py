"""Where files live on disk, and how they get written safely.

This layer knows paths and bytes. It never touches the network -- see
``fetcher.py`` for that half.

Layout::

    {data_dir}/raw/{key}/{YYYY}/{YYYY-MM-DD}.{ext}
    {data_dir}/raw/_other/...          (Phase 2)
    {data_dir}/manifest.db
    {data_dir}/logs/{YYYY-MM-DD}.log
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

log = logging.getLogger("psx.store")

PART_SUFFIX = ".part"

#: Reserved for the Phase 2 "Other Downloads" snapshot; skipped by the walk
#: that rebuilds the manifest, which only understands date-keyed files.
OTHER_DIR = "_other"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What landed on disk."""

    path: Path
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class StoredFile:
    """A date-keyed file found on disk by :meth:`Store.iter_existing`."""

    dataset: str
    trade_date: date
    ext: str
    path: Path
    bytes: int


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Hash a file without reading it all into memory (used by ``reconcile``)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


class Store:
    """Path building and atomic writes under one data directory."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)

    # --- locations ---------------------------------------------------------

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def other_dir(self) -> Path:
        return self.raw_dir / OTHER_DIR

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.db"

    def path_for(self, dataset: str, trade_date: date, ext: str) -> Path:
        """Absolute path for one daily file."""
        return self.raw_dir / dataset / f"{trade_date.year:04d}" / f"{trade_date.isoformat()}.{ext}"

    # --- queries -----------------------------------------------------------

    def exists(self, dataset: str, trade_date: date, ext: str) -> bool:
        """True only for a real, non-empty file. A 0-byte file is not a download."""
        path = self.path_for(dataset, trade_date, ext)
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:  # pragma: no cover - defensive
            return False

    def size_of(self, dataset: str, trade_date: date, ext: str) -> int:
        path = self.path_for(dataset, trade_date, ext)
        return path.stat().st_size if path.is_file() else 0

    # --- writing -----------------------------------------------------------

    def write(self, dataset: str, trade_date: date, ext: str, data: bytes) -> WriteResult:
        """Atomically store one file's bytes.

        Writes ``*.part`` first and renames only once the bytes are flushed to
        disk, so an interrupted run never leaves a half-written archive file.
        """
        target = self.path_for(dataset, trade_date, ext)
        return self.write_path(target, data)

    def write_path(self, target: Path, data: bytes) -> WriteResult:
        """Atomic write to an arbitrary path inside the store."""
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + PART_SUFFIX)
        try:
            with partial.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(partial, target)  # atomic on the same volume, incl. Windows
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return WriteResult(path=target, bytes=len(data), sha256=sha256_bytes(data))

    def sweep_partials(self) -> int:
        """Delete leftover ``*.part`` files from a killed run. Returns the count."""
        if not self.raw_dir.exists():
            return 0
        removed = 0
        for partial in self.raw_dir.rglob("*" + PART_SUFFIX):
            try:
                partial.unlink()
                removed += 1
                log.debug("removed stale partial %s", partial)
            except OSError as exc:  # pragma: no cover - defensive
                log.warning("could not remove %s: %s", partial, exc)
        return removed

    # --- walking -----------------------------------------------------------

    def iter_existing(self, dataset: str | None = None) -> Iterator[StoredFile]:
        """Yield every date-keyed file on disk. This is what ``reconcile`` reads.

        Anything that does not match the naming scheme -- ``.part`` files, the
        ``_other`` tree, stray files -- is skipped rather than guessed at.
        """
        if not self.raw_dir.exists():
            return

        for dataset_dir in sorted(p for p in self.raw_dir.iterdir() if p.is_dir()):
            if dataset_dir.name == OTHER_DIR:
                continue
            if dataset is not None and dataset_dir.name != dataset:
                continue

            for year_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
                if not _YEAR_RE.match(year_dir.name):
                    log.debug("skipping non-year directory %s", year_dir)
                    continue

                for path in sorted(p for p in year_dir.iterdir() if p.is_file()):
                    parsed = self._parse_file(dataset_dir.name, path)
                    if parsed is not None:
                        yield parsed

    @staticmethod
    def _parse_file(dataset: str, path: Path) -> StoredFile | None:
        if path.name.endswith(PART_SUFFIX):
            return None
        stem, dot, ext = path.name.partition(".")
        if not dot or not ext or not _DATE_RE.match(stem):
            log.debug("skipping unrecognised file %s", path)
            return None
        try:
            trade_date = date.fromisoformat(stem)
        except ValueError:
            log.debug("skipping file with an impossible date %s", path)
            return None
        return StoredFile(
            dataset=dataset,
            trade_date=trade_date,
            ext=ext,
            path=path,
            bytes=path.stat().st_size,
        )
