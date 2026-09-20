"""Step 8 — the GUI's non-visual logic.

The layout itself is checked by hand (`streamlit run app.py`). What is tested
here is the part that would silently break a job: the shared state between the
worker thread and the page, and the fact that the GUI drives the same
``jobs.run`` the CLI does.
"""

from __future__ import annotations

import threading
from datetime import date

import pytest

import app as app_module
from psx.jobs import JobSummary
from psx.manifest import ERROR, MISSING, OK
from psx.registry import Dataset

THU = date(2026, 9, 17)


@pytest.fixture
def state() -> app_module.JobState:
    return app_module.JobState()


# --- shared state -----------------------------------------------------------


def test_a_new_state_is_idle(state: app_module.JobState) -> None:
    snap = state.snapshot()
    assert snap["running"] is False
    assert snap["done"] == 0
    assert snap["summary"] is None


def test_start_resets_the_counters(state: app_module.JobState) -> None:
    state.on_progress("omts", THU, "csv", OK)
    state.finish(JobSummary(ok=1))

    state.start(planned=10)

    snap = state.snapshot()
    assert snap["running"] is True
    assert snap["planned"] == 10
    assert snap["done"] == 0
    assert snap["counts"][OK] == 0
    assert snap["summary"] is None


def test_progress_tallies_each_status(state: app_module.JobState) -> None:
    state.start(planned=4)
    state.on_progress("omts", THU, "csv", OK)
    state.on_progress("omts", THU, "csv", MISSING)
    state.on_progress("omts", THU, "csv", ERROR)
    state.on_progress("omts", THU, "csv", app_module.jobs.SKIPPED)

    snap = state.snapshot()
    assert snap["done"] == 4
    assert snap["counts"] == {OK: 1, MISSING: 1, ERROR: 1, app_module.jobs.SKIPPED: 1}


def test_skipped_files_stay_out_of_the_live_log(state: app_module.JobState) -> None:
    """A resumed backfill would otherwise flood the log with skips."""
    state.start(planned=2)
    state.on_progress("omts", THU, "csv", app_module.jobs.SKIPPED)
    state.on_progress("omts", THU, "csv", OK)

    log = state.snapshot()["log"]
    assert len(log) == 1
    assert "ok" in log[0]


def test_the_log_is_newest_first_and_bounded(state: app_module.JobState) -> None:
    state.start(planned=1000)
    for day in range(1, 30):
        state.on_progress("omts", date(2026, 9, 1) + __import__("datetime").timedelta(days=day - 1), "csv", OK)

    log = state.snapshot()["log"]
    assert "2026-09-29" in log[0]
    assert len(log) <= app_module.MAX_LOG_LINES


def test_cancellation_is_visible_to_the_worker(state: app_module.JobState) -> None:
    state.start(planned=5)
    assert state.should_cancel() is False

    state.request_cancel()

    assert state.should_cancel() is True
    assert "stopping" in state.snapshot()["log"][0]


def test_finish_records_the_summary(state: app_module.JobState) -> None:
    state.start(planned=1)
    state.finish(JobSummary(ok=1, requests_made=1))

    snap = state.snapshot()
    assert snap["running"] is False
    assert snap["summary"].ok == 1
    assert snap["failure"] is None


def test_a_crash_in_the_worker_is_recorded_not_swallowed(state: app_module.JobState) -> None:
    state.start(planned=1)
    state.finish(None, failure="RuntimeError: boom")

    snap = state.snapshot()
    assert snap["running"] is False
    assert snap["failure"] == "RuntimeError: boom"


def test_concurrent_progress_updates_are_not_lost(state: app_module.JobState) -> None:
    """The counters are touched from another thread, so they are locked."""
    state.start(planned=200)

    def bump() -> None:
        for _ in range(100):
            state.on_progress("omts", THU, "csv", OK)

    threads = [threading.Thread(target=bump) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state.snapshot()["done"] == 200


# --- the GUI runs the same job the CLI does ---------------------------------


def test_start_job_calls_jobs_run_on_a_background_thread(monkeypatch, settings) -> None:
    called: dict = {}
    finished = threading.Event()

    def fake_run(datasets, date_from, date_to, **kwargs):
        called["datasets"] = [d.key for d in datasets]
        called["range"] = (date_from, date_to)
        called["exts"] = kwargs.get("exts")
        called["force"] = kwargs.get("force")
        called["has_progress_cb"] = kwargs.get("progress_cb") is not None
        called["has_cancel_cb"] = kwargs.get("cancel_cb") is not None
        finished.set()
        return JobSummary(ok=1)

    monkeypatch.setattr(app_module.jobs, "run", fake_run)

    state = app_module.JobState()
    omts = Dataset(key="omts", label="x", section="s", exts=("csv",))
    app_module.start_job(state, [omts], THU, THU, settings, None, False)

    assert finished.wait(timeout=5)
    state.thread.join(timeout=5)

    assert called["datasets"] == ["omts"]
    assert called["range"] == (THU, THU)
    assert called["has_progress_cb"] and called["has_cancel_cb"]
    assert state.snapshot()["summary"].ok == 1


def test_a_worker_exception_lands_in_the_ui_state(monkeypatch, settings) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("network gone")

    monkeypatch.setattr(app_module.jobs, "run", explode)

    state = app_module.JobState()
    omts = Dataset(key="omts", label="x", section="s", exts=("csv",))
    app_module.start_job(state, [omts], THU, THU, settings, None, False)
    state.thread.join(timeout=5)

    snap = state.snapshot()
    assert snap["running"] is False
    assert "RuntimeError: network gone" in snap["failure"]


# --- small helpers ----------------------------------------------------------


def test_quick_ranges_end_on_a_trading_day(settings) -> None:
    for label in ["Last 5 days", "Last 30 days", "This month", "YTD", "Max (10 years)"]:
        start, end = app_module.quick_range(label, settings)
        assert start <= end
        assert end.weekday() < 5


def test_max_range_goes_ten_years_back(settings) -> None:
    start, end = app_module.quick_range("Max (10 years)", settings)
    assert start.year == end.year - app_module.YEARS_BACK


def test_ytd_starts_in_january(settings) -> None:
    start, end = app_module.quick_range("YTD", settings)
    assert (start.month, start.day) == (1, 1)
    assert start.year == end.year


@pytest.mark.parametrize(
    ("value", "expected_fragment"),
    [("ok 21", "#1b5e20"), ("err 3", "#8b0000"), ("miss 4", "#5d4037"), ("—", "#bbb")],
)
def test_heatmap_colours(value: str, expected_fragment: str) -> None:
    assert expected_fragment in app_module.colour(value)
