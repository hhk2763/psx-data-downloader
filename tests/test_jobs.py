"""Step 6 — the orchestrator: resume, politeness, cancellation, abort.

These cover the behaviour CLAUDE.md calls non-negotiable. A fake fetcher stands
in for the network so the tests are exact about how many requests happen.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from psx import jobs
from psx.fetcher import OCTET_STREAM, FetchOutcome
from psx.jobs import JobSummary, estimate, plan_units, run
from psx.manifest import ERROR, MISSING, OK, LogEntry, Manifest
from psx.registry import Dataset
from psx.store import Store

MON = date(2026, 9, 14)
THU = date(2026, 9, 17)
FRI = date(2026, 9, 18)
SAT = date(2026, 9, 19)
SUN = date(2026, 9, 20)
NEXT_MON = date(2026, 9, 21)


class FakeFetcher:
    """Records every URL it is asked for and replies from a script."""

    def __init__(self, default: FetchOutcome | None = None, script: dict[str, FetchOutcome] | None = None) -> None:
        self.default = default or FetchOutcome(
            status=OK, http_code=200, content_type=OCTET_STREAM, body=b"file bytes"
        )
        self.script = script or {}
        self.urls: list[str] = []
        self.closed = False

    def fetch(self, url: str) -> FetchOutcome:
        self.urls.append(url)
        for fragment, outcome in self.script.items():
            if fragment in url:
                return outcome
        return self.default

    def close(self) -> None:
        self.closed = True


MISSING_OUTCOME = FetchOutcome(status=MISSING, http_code=404, content_type="text/html")
ERROR_OUTCOME = FetchOutcome(status=ERROR, http_code=500, error_msg="HTTP 500")


@pytest.fixture
def store(settings) -> Store:
    return Store(settings.data_dir)


@pytest.fixture
def manifest(store: Store):
    with Manifest(store.manifest_path) as m:
        yield m


def go(datasets, date_from, date_to, *, settings, store, manifest, fetcher, **kw) -> JobSummary:
    return run(
        datasets if isinstance(datasets, (list, tuple)) else [datasets],
        date_from,
        date_to,
        settings=settings,
        store=store,
        manifest=manifest,
        fetcher=fetcher,
        today=FRI,
        **kw,
    )


# --- planning ---------------------------------------------------------------


def test_plan_units_is_dataset_major(omts: Dataset, fut_opn_int: Dataset) -> None:
    units = plan_units([omts, fut_opn_int], THU, FRI)
    assert [str(u) for u in units] == [
        "omts 2026-09-17 .csv",
        "omts 2026-09-18 .csv",
        "fut_opn_int 2026-09-17 .pdf",
        "fut_opn_int 2026-09-17 .xls",
        "fut_opn_int 2026-09-18 .pdf",
        "fut_opn_int 2026-09-18 .xls",
    ]


def test_plan_units_excludes_weekends(omts: Dataset) -> None:
    units = plan_units([omts], FRI, NEXT_MON)
    assert [u.trade_date for u in units] == [FRI, NEXT_MON]


def test_plan_units_filters_by_extension(fut_opn_int: Dataset) -> None:
    units = plan_units([fut_opn_int], THU, THU, exts=["xls"])
    assert [u.ext for u in units] == ["xls"]


def test_plan_units_drops_a_dataset_with_no_matching_extension(omts: Dataset) -> None:
    assert plan_units([omts], THU, THU, exts=["xls"]) == []


def test_plan_units_respects_first_available(omts: Dataset) -> None:
    dated = Dataset(key=omts.key, label=omts.label, section=omts.section, exts=omts.exts, first_available=FRI)
    assert [u.trade_date for u in plan_units([dated], THU, FRI)] == [FRI]


def test_estimate_scales_with_the_delay(settings) -> None:
    est = estimate(100, settings)
    assert est.requests == 100
    assert est.seconds == pytest.approx(100 * (settings.delay_seconds + settings.jitter_seconds / 2))


def test_estimate_formats_long_runs() -> None:
    from psx.config import Settings

    slow = Settings(delay_seconds=1.5, jitter_seconds=0.5)
    assert estimate(60000, slow).human_time == "29h 10m"


# --- the happy path ---------------------------------------------------------


def test_a_successful_fetch_writes_the_file_and_records_it(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher()

    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=fetcher)

    assert summary.ok == 1
    assert summary.requests_made == 1
    assert store.path_for("omts", THU, "csv").read_bytes() == b"file bytes"

    entry = manifest.get("omts", THU, "csv")
    assert entry is not None and entry.status == OK
    assert entry.bytes == len(b"file bytes")
    assert entry.sha256


def test_the_url_follows_the_documented_pattern(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher()
    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=fetcher)
    assert fetcher.urls == ["https://dps.psx.com.pk/download/omts/2026-09-17.csv"]


def test_a_multi_extension_dataset_fetches_every_extension(settings, store, manifest, fut_opn_int) -> None:
    fetcher = FakeFetcher()

    summary = go(fut_opn_int, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=fetcher)

    assert summary.ok == 2
    assert store.exists("fut_opn_int", THU, "pdf")
    assert store.exists("fut_opn_int", THU, "xls")


def test_the_extension_filter_narrows_the_run(settings, store, manifest, fut_opn_int) -> None:
    fetcher = FakeFetcher()

    summary = go(fut_opn_int, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=fetcher, exts=["pdf"])

    assert summary.ok == 1
    assert store.exists("fut_opn_int", THU, "pdf")
    assert not store.exists("fut_opn_int", THU, "xls")


def test_bytes_downloaded_is_tallied(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher()
    summary = go(omts, THU, FRI, settings=settings, store=store, manifest=manifest, fetcher=fetcher)
    assert summary.bytes_downloaded == 2 * len(b"file bytes")


# --- weekends ---------------------------------------------------------------


def test_weekends_are_skipped_without_a_request(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher()

    summary = go(omts, SAT, SUN, settings=settings, store=store, manifest=manifest, fetcher=fetcher)

    assert fetcher.urls == []
    assert summary.planned == 0
    assert summary.requests_made == 0


def test_a_range_spanning_a_weekend_requests_only_the_weekdays(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher()

    summary = go(omts, FRI, NEXT_MON, settings=settings, store=store, manifest=manifest, fetcher=fetcher)

    assert summary.requests_made == 2
    assert all("2026-09-19" not in u and "2026-09-20" not in u for u in fetcher.urls)


# --- missing and error ------------------------------------------------------


def test_a_404_records_missing_and_writes_nothing(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher(default=MISSING_OUTCOME)

    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=fetcher)

    assert summary.missing == 1
    assert summary.ok == 0
    assert not store.path_for("omts", THU, "csv").exists()
    entry = manifest.get("omts", THU, "csv")
    assert entry is not None and entry.status == MISSING


def test_an_error_records_error_and_writes_nothing(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher(default=ERROR_OUTCOME)

    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=fetcher)

    assert summary.error == 1
    assert not store.path_for("omts", THU, "csv").exists()
    entry = manifest.get("omts", THU, "csv")
    assert entry is not None and entry.status == ERROR
    assert entry.error_msg == "HTTP 500"
    assert summary.errors


def test_no_part_file_survives_any_outcome(settings, store, manifest, fut_opn_int) -> None:
    fetcher = FakeFetcher(script={".xls": ERROR_OUTCOME})
    go(fut_opn_int, THU, FRI, settings=settings, store=store, manifest=manifest, fetcher=fetcher)
    assert list(store.raw_dir.rglob("*.part")) == []


# --- resume -----------------------------------------------------------------


def test_a_second_run_re_downloads_nothing(settings, store, manifest, omts) -> None:
    first = FakeFetcher()
    go(omts, MON, FRI, settings=settings, store=store, manifest=manifest, fetcher=first)
    assert first.urls and len(first.urls) == 5

    second = FakeFetcher()
    summary = go(omts, MON, FRI, settings=settings, store=store, manifest=manifest, fetcher=second)

    assert second.urls == []
    assert summary.skipped_existing == 5
    assert summary.requests_made == 0


def test_resuming_after_a_kill_fetches_only_what_is_left(settings, store, manifest, omts) -> None:
    """Simulates Ctrl-C: the first run stops after two days."""
    calls = {"n": 0}

    def cancel_after_two() -> bool:
        cancel = calls["n"] >= 2
        calls["n"] += 1
        return cancel

    first = FakeFetcher()
    partial = go(
        omts, MON, FRI, settings=settings, store=store, manifest=manifest, fetcher=first, cancel_cb=cancel_after_two
    )
    assert partial.cancelled
    assert partial.ok == 2

    second = FakeFetcher()
    summary = go(omts, MON, FRI, settings=settings, store=store, manifest=manifest, fetcher=second)

    assert summary.ok == 3
    assert summary.skipped_existing == 2
    assert len(second.urls) == 3


def test_force_refetches_files_already_recorded_ok(settings, store, manifest, omts) -> None:
    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher())

    again = FakeFetcher(default=FetchOutcome(status=OK, http_code=200, content_type=OCTET_STREAM, body=b"newer"))
    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=again, force=True)

    assert summary.ok == 1
    assert summary.skipped_existing == 0
    assert store.path_for("omts", THU, "csv").read_bytes() == b"newer"


def test_a_recent_missing_date_is_rechecked(settings, store, manifest, omts) -> None:
    """PSX publishes late sometimes, so the last few days stay live."""
    missing_run = FakeFetcher(default=MISSING_OUTCOME)
    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=missing_run)

    later = FakeFetcher()
    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=later)

    assert summary.ok == 1
    assert len(later.urls) == 1


def test_an_old_missing_date_is_not_rechecked(settings, store, manifest, omts) -> None:
    old = date(2026, 8, 3)
    manifest.record(LogEntry(dataset="omts", trade_date=old, ext="csv", status=MISSING, http_code=404))

    fetcher = FakeFetcher()
    summary = run(
        [omts], old, old, settings=settings, store=store, manifest=manifest, fetcher=fetcher, today=FRI
    )

    assert fetcher.urls == []
    assert summary.skipped_existing == 1


def test_an_error_is_retried_on_the_next_run(settings, store, manifest, omts) -> None:
    failed = FakeFetcher(default=ERROR_OUTCOME)
    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=failed)

    retry = FakeFetcher()
    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=retry)

    assert summary.ok == 1
    assert len(retry.urls) == 1


def test_the_disk_wins_when_the_manifest_disagrees(settings, store, manifest, omts) -> None:
    """Manifest says ok, but the file was deleted: fetch it again."""
    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher())
    store.path_for("omts", THU, "csv").unlink()

    again = FakeFetcher()
    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=again)

    assert summary.ok == 1
    assert len(again.urls) == 1
    assert store.exists("omts", THU, "csv")


def test_a_zero_byte_file_is_treated_as_absent(settings, store, manifest, omts) -> None:
    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher())
    path = store.path_for("omts", THU, "csv")
    path.write_bytes(b"")

    again = FakeFetcher()
    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=again)

    assert summary.ok == 1
    assert path.stat().st_size > 0


# --- stopping -----------------------------------------------------------------


def test_ten_consecutive_errors_abort_the_job(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher(default=ERROR_OUTCOME)

    summary = run(
        [omts], date(2026, 1, 1), FRI, settings=settings, store=store, manifest=manifest, fetcher=fetcher, today=FRI
    )

    assert summary.aborted
    assert summary.abort_reason == "10 consecutive errors"
    assert summary.error == 10
    assert len(fetcher.urls) == 10
    assert summary.planned > 10  # it stopped early on purpose


def test_the_error_streak_resets_after_a_success(settings, store, manifest, omts) -> None:
    """Scattered failures must not abort a long backfill."""
    script = {f"2026-09-{d:02d}": ERROR_OUTCOME for d in (14, 15, 16, 17)}
    fetcher = FakeFetcher(script=script)

    summary = run(
        [omts], MON, NEXT_MON, settings=settings, store=store, manifest=manifest, fetcher=fetcher, today=FRI
    )

    assert not summary.aborted
    assert summary.error == 4
    assert summary.ok == 2


def test_cancellation_is_checked_between_requests(settings, store, manifest, omts) -> None:
    fetcher = FakeFetcher()

    summary = go(
        omts, MON, FRI, settings=settings, store=store, manifest=manifest, fetcher=fetcher, cancel_cb=lambda: True
    )

    assert summary.cancelled
    assert fetcher.urls == []
    assert summary.requests_made == 0


def test_a_keyboard_interrupt_ends_the_run_cleanly(settings, store, manifest, omts) -> None:
    class Interrupting(FakeFetcher):
        def fetch(self, url: str) -> FetchOutcome:
            if len(self.urls) >= 2:
                raise KeyboardInterrupt
            return super().fetch(url)

    summary = go(omts, MON, FRI, settings=settings, store=store, manifest=manifest, fetcher=Interrupting())

    assert summary.cancelled
    assert summary.ok == 2


# --- reporting ----------------------------------------------------------------


def test_progress_is_reported_once_per_unit(settings, store, manifest, fut_opn_int) -> None:
    seen: list[tuple[str, date, str, str]] = []
    fetcher = FakeFetcher(script={".xls": MISSING_OUTCOME})

    go(
        fut_opn_int,
        THU,
        THU,
        settings=settings,
        store=store,
        manifest=manifest,
        fetcher=fetcher,
        progress_cb=lambda ds, d, ext, status: seen.append((ds, d, ext, status)),
    )

    assert seen == [
        ("fut_opn_int", THU, "pdf", OK),
        ("fut_opn_int", THU, "xls", MISSING),
    ]


def test_skipped_units_are_reported_too(settings, store, manifest, omts) -> None:
    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher())

    seen: list[str] = []
    go(
        omts,
        THU,
        THU,
        settings=settings,
        store=store,
        manifest=manifest,
        fetcher=FakeFetcher(),
        progress_cb=lambda ds, d, ext, status: seen.append(status),
    )

    assert seen == [jobs.SKIPPED]


def test_a_broken_progress_callback_does_not_kill_the_job(settings, store, manifest, omts) -> None:
    def broken(*args: object) -> None:
        raise RuntimeError("GUI went away")

    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher(), progress_cb=broken)

    assert summary.ok == 1


def test_summary_line_and_success_flag(settings, store, manifest, omts) -> None:
    summary = go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher())
    assert summary.succeeded
    assert "ok=1" in summary.as_line()

    failed = go(omts, FRI, FRI, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher(default=ERROR_OUTCOME))
    assert not failed.succeeded


def test_leftover_part_files_are_swept_before_the_run(settings, store, manifest, omts) -> None:
    stale = store.path_for("omts", MON, "csv")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.with_name(stale.name + ".part").write_bytes(b"half a file")

    go(omts, THU, THU, settings=settings, store=store, manifest=manifest, fetcher=FakeFetcher())

    assert list(store.raw_dir.rglob("*.part")) == []


def test_run_builds_its_own_collaborators_when_none_are_given(settings, omts, monkeypatch) -> None:
    """The CLI and the GUI both call run() with just settings."""
    fetcher = FakeFetcher()
    monkeypatch.setattr(jobs, "build_fetcher", lambda s, **kw: fetcher)

    summary = run([omts], THU, THU, settings=settings, today=FRI)

    assert summary.ok == 1
    assert fetcher.closed
    assert Store(settings.data_dir).exists("omts", THU, "csv")
