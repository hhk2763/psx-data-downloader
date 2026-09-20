"""The orchestrator. Both ``cli.py`` and ``app.py`` call :func:`run`.

Nothing else in the project drives a download loop: the GUI holds no fetch
logic of its own, so the CLI and the GUI cannot drift apart.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable, Sequence

from psx.calendar import iter_trading_days, today_pkt
from psx.config import Settings
from psx.fetcher import Fetcher, build_fetcher
from psx.manifest import ERROR, MISSING, OK, LogEntry, Manifest, Status, utc_now_iso
from psx.registry import Dataset
from psx.store import Store

log = logging.getLogger("psx.jobs")

#: ``progress_cb(dataset_key, trade_date, ext, status)``. ``status`` is one of
#: ok / missing / error / skipped.
ProgressCB = Callable[[str, date, str, str], None]
CancelCB = Callable[[], bool]

SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Unit:
    """One file to consider: a dataset, a date and an extension."""

    dataset: Dataset
    trade_date: date
    ext: str

    @property
    def key(self) -> str:
        return self.dataset.key

    def __str__(self) -> str:
        return f"{self.dataset.key} {self.trade_date.isoformat()} .{self.ext}"


@dataclass(slots=True)
class JobSummary:
    """Counts for one ``run()``. Mutated as the job proceeds."""

    planned: int = 0
    ok: int = 0
    missing: int = 0
    error: int = 0
    skipped_existing: int = 0
    requests_made: int = 0
    bytes_downloaded: int = 0
    elapsed_seconds: float = 0.0
    aborted: bool = False
    abort_reason: str | None = None
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return self.ok + self.missing + self.error + self.skipped_existing

    @property
    def succeeded(self) -> bool:
        """A clean run: nothing failed, and it was not stopped early."""
        return not self.aborted and not self.cancelled and self.error == 0

    def as_line(self) -> str:
        parts = [
            f"ok={self.ok}",
            f"missing={self.missing}",
            f"error={self.error}",
            f"skipped={self.skipped_existing}",
            f"requests={self.requests_made}",
            f"elapsed={self.elapsed_seconds:.1f}s",
        ]
        if self.cancelled:
            parts.append("CANCELLED")
        if self.aborted:
            parts.append(f"ABORTED ({self.abort_reason})")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class Estimate:
    """What a run will cost, shown before it starts."""

    requests: int
    seconds: float

    @property
    def human_time(self) -> str:
        seconds = int(round(self.seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"


# --- planning ---------------------------------------------------------------


def plan_units(
    datasets: Sequence[Dataset],
    date_from: date,
    date_to: date,
    *,
    exts: Sequence[str] | None = None,
) -> list[Unit]:
    """Every (dataset, trading day, extension) the range implies.

    Weekends never appear, so they cost no request. Dates a dataset is known
    not to cover (``first_available``) are dropped here too.

    Dataset-major order, which matches how backfills are actually run: one
    dataset or section at a time.
    """
    wanted = {e.lower() for e in exts} if exts else None
    units: list[Unit] = []
    for dataset in datasets:
        selected = [e for e in dataset.exts if wanted is None or e.lower() in wanted]
        if not selected:
            continue
        for trade_date in iter_trading_days(date_from, date_to):
            if not dataset.covers(trade_date):
                continue
            units.extend(Unit(dataset, trade_date, ext) for ext in selected)
    return units


def needs_fetch(
    unit: Unit,
    *,
    manifest: Manifest,
    store: Store,
    force: bool = False,
    recheck_days: int = 3,
    today: date | None = None,
) -> bool:
    """Whether this unit is worth a request.

    The manifest decides, with one override: if it claims ``ok`` but the file
    is not on disk, the disk wins and we fetch again.
    """
    if force:
        return True

    if manifest.should_fetch(
        unit.key, unit.trade_date, unit.ext, force=False, recheck_days=recheck_days, today=today
    ):
        return True

    entry = manifest.get(unit.key, unit.trade_date, unit.ext)
    if entry is not None and entry.status == OK and not store.exists(unit.key, unit.trade_date, unit.ext):
        log.info("%s: manifest says ok but the file is gone; re-fetching", unit)
        return True
    return False


def pending_units(
    units: Iterable[Unit],
    *,
    manifest: Manifest,
    store: Store,
    force: bool = False,
    recheck_days: int = 3,
    today: date | None = None,
) -> list[Unit]:
    """The subset of ``units`` that would actually hit the network."""
    return [
        u
        for u in units
        if needs_fetch(u, manifest=manifest, store=store, force=force, recheck_days=recheck_days, today=today)
    ]


def estimate(request_count: int, settings: Settings) -> Estimate:
    """Time a run will take at the configured delay (excludes transfer time)."""
    per_request = settings.delay_seconds + settings.jitter_seconds / 2
    return Estimate(requests=request_count, seconds=request_count * per_request)


# --- the run ----------------------------------------------------------------


def run(
    datasets: Sequence[Dataset],
    date_from: date,
    date_to: date,
    *,
    settings: Settings,
    exts: Sequence[str] | None = None,
    force: bool = False,
    progress_cb: ProgressCB | None = None,
    cancel_cb: CancelCB | None = None,
    store: Store | None = None,
    manifest: Manifest | None = None,
    fetcher: Fetcher | None = None,
    today: date | None = None,
) -> JobSummary:
    """Download every file the arguments imply, politely and resumably.

    Safe to kill and restart: anything already recorded ``ok`` with a file on
    disk is skipped without a request.
    """
    started = time.monotonic()
    today = today or today_pkt()

    owns_store = store is None
    owns_manifest = manifest is None
    owns_fetcher = fetcher is None

    store = store or Store(settings.data_dir)
    manifest = manifest or Manifest(store.manifest_path)
    fetcher = fetcher or build_fetcher(settings)

    units = plan_units(datasets, date_from, date_to, exts=exts)
    summary = JobSummary(planned=len(units))

    log.info(
        "job: %d dataset(s), %s..%s, %d file(s) planned",
        len(datasets),
        date_from.isoformat(),
        date_to.isoformat(),
        len(units),
    )

    try:
        removed = store.sweep_partials()
        if removed:
            log.info("cleaned up %d leftover .part file(s) from a previous run", removed)

        consecutive_errors = 0

        for unit in units:
            if cancel_cb is not None and cancel_cb():
                summary.cancelled = True
                log.info("cancelled by request after %d file(s)", summary.processed)
                break

            if not needs_fetch(
                unit,
                manifest=manifest,
                store=store,
                force=force,
                recheck_days=settings.recheck_days,
                today=today,
            ):
                summary.skipped_existing += 1
                log.debug("%s: skipped, already recorded", unit)
                _report(progress_cb, unit, SKIPPED)
                continue

            status = _fetch_one(unit, settings=settings, store=store, manifest=manifest, fetcher=fetcher, summary=summary)

            if status == ERROR:
                consecutive_errors += 1
            else:
                consecutive_errors = 0

            _report(progress_cb, unit, status)

            if consecutive_errors >= settings.max_consecutive_errors:
                summary.aborted = True
                summary.abort_reason = f"{consecutive_errors} consecutive errors"
                log.error("stopping: %s. Check the site or your connection before retrying.", summary.abort_reason)
                break

    except KeyboardInterrupt:
        summary.cancelled = True
        log.warning("interrupted; stopping after %d file(s)", summary.processed)
    finally:
        summary.elapsed_seconds = time.monotonic() - started
        if owns_fetcher:
            fetcher.close()
        if owns_manifest:
            manifest.close()
        log.info("done: %s", summary.as_line())

    return summary


def _fetch_one(
    unit: Unit,
    *,
    settings: Settings,
    store: Store,
    manifest: Manifest,
    fetcher: Fetcher,
    summary: JobSummary,
) -> Status:
    """One request, its bookkeeping, and the resulting status."""
    url = settings.download_url(unit.key, unit.trade_date, unit.ext)
    outcome = fetcher.fetch(url)
    summary.requests_made += 1

    if outcome.is_ok and outcome.body is not None:
        try:
            written = store.write(unit.key, unit.trade_date, unit.ext, outcome.body)
        except OSError as exc:
            message = f"write failed: {exc}"
            log.error("%s: %s", unit, message)
            summary.error += 1
            summary.errors.append(f"{unit}: {message}")
            manifest.record(
                LogEntry(
                    dataset=unit.key,
                    trade_date=unit.trade_date,
                    ext=unit.ext,
                    status=ERROR,
                    http_code=outcome.http_code,
                    error_msg=message,
                    fetched_at=utc_now_iso(),
                )
            )
            return ERROR

        summary.ok += 1
        summary.bytes_downloaded += written.bytes
        manifest.record(
            LogEntry(
                dataset=unit.key,
                trade_date=unit.trade_date,
                ext=unit.ext,
                status=OK,
                http_code=outcome.http_code,
                bytes=written.bytes,
                sha256=written.sha256,
                fetched_at=utc_now_iso(),
            )
        )
        log.debug("%s: ok, %d bytes", unit, written.bytes)
        return OK

    if outcome.status == MISSING:
        summary.missing += 1
        manifest.record(
            LogEntry(
                dataset=unit.key,
                trade_date=unit.trade_date,
                ext=unit.ext,
                status=MISSING,
                http_code=outcome.http_code,
                fetched_at=utc_now_iso(),
            )
        )
        log.debug("%s: missing (holiday, or not published)", unit)
        return MISSING

    summary.error += 1
    summary.errors.append(f"{unit}: {outcome.error_msg}")
    manifest.record(
        LogEntry(
            dataset=unit.key,
            trade_date=unit.trade_date,
            ext=unit.ext,
            status=ERROR,
            http_code=outcome.http_code,
            error_msg=outcome.error_msg,
            fetched_at=utc_now_iso(),
        )
    )
    log.warning("%s: error (%s)", unit, outcome.error_msg)
    return ERROR


def _report(progress_cb: ProgressCB | None, unit: Unit, status: str) -> None:
    """Progress reporting must never take the job down."""
    if progress_cb is None:
        return
    try:
        progress_cb(unit.key, unit.trade_date, unit.ext, status)
    except Exception:  # pragma: no cover - defensive
        log.debug("progress callback raised; continuing", exc_info=True)
