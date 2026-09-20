"""Streamlit GUI — local use only (``streamlit run app.py``).

This page holds no fetch logic. Every download goes through ``jobs.run()``,
exactly as the CLI does, so the two can never drift apart.

The job runs on a background thread so the UI stays responsive. The thread
touches only :class:`JobState` -- never ``st.*`` -- and the page polls it.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st

from psx import jobs
from psx.calendar import last_completed_trading_day, range_for_last_n
from psx.config import DEFAULT_CONFIG_DIR, ConfigError, configure_logging, load_settings
from psx.manifest import ERROR, MISSING, OK, Manifest
from psx.registry import Dataset, RegistryError, load_registry
from psx.store import Store

MAX_LOG_LINES = 400
YEARS_BACK = 10

st.set_page_config(page_title="PSX Data Downloader", page_icon="📈", layout="wide")


class JobState:
    """Shared state between the worker thread and the page.

    Plain locking, no Streamlit APIs: a background thread has no script
    context, so it must not call ``st.*``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.running = False
        self.cancel_requested = False
        self.planned = 0
        self.done = 0
        self.counts = {OK: 0, MISSING: 0, ERROR: 0, jobs.SKIPPED: 0}
        self.log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self.summary: jobs.JobSummary | None = None
        self.failure: str | None = None

    # -- worker side ----------------------------------------------------

    def start(self, planned: int) -> None:
        with self._lock:
            self.running = True
            self.cancel_requested = False
            self.planned = planned
            self.done = 0
            self.counts = {OK: 0, MISSING: 0, ERROR: 0, jobs.SKIPPED: 0}
            self.log.clear()
            self.summary = None
            self.failure = None

    def on_progress(self, dataset_key: str, trade_date: date, ext: str, status: str) -> None:
        with self._lock:
            self.done += 1
            self.counts[status] = self.counts.get(status, 0) + 1
            if status != jobs.SKIPPED:
                self.log.appendleft(f"{dataset_key} {trade_date.isoformat()}.{ext} — {status}")

    def finish(self, summary: jobs.JobSummary | None, failure: str | None = None) -> None:
        with self._lock:
            self.running = False
            self.summary = summary
            self.failure = failure

    # -- page side ------------------------------------------------------

    def request_cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True
            self.log.appendleft("stopping after the current request...")

    def should_cancel(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "planned": self.planned,
                "done": self.done,
                "counts": dict(self.counts),
                "log": list(self.log),
                "summary": self.summary,
                "failure": self.failure,
                "cancel_requested": self.cancel_requested,
            }


def get_state() -> JobState:
    if "job_state" not in st.session_state:
        st.session_state.job_state = JobState()
    return st.session_state.job_state


@st.cache_resource
def load_config(config_dir: str):
    """Settings and registry, cached for the session."""
    settings = load_settings(Path(config_dir) / "settings.yaml")
    registry = load_registry(Path(config_dir) / "registry.yaml")
    return settings, registry


def start_job(
    state: JobState,
    datasets: list[Dataset],
    date_from: date,
    date_to: date,
    settings,
    exts: list[str] | None,
    force: bool,
) -> None:
    """Kick off ``jobs.run`` on a background thread."""
    planned = len(jobs.plan_units(datasets, date_from, date_to, exts=exts))
    state.start(planned)

    def worker() -> None:
        try:
            summary = jobs.run(
                datasets,
                date_from,
                date_to,
                settings=settings,
                exts=exts,
                force=force,
                progress_cb=state.on_progress,
                cancel_cb=state.should_cancel,
            )
            state.finish(summary)
        except Exception as exc:  # surfaced in the UI rather than lost in a thread
            state.finish(None, failure=f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=worker, name="psx-job", daemon=True)
    state.thread = thread
    thread.start()


# --- Download ---------------------------------------------------------------


def quick_range(label: str, settings) -> tuple[date, date]:
    end = last_completed_trading_day(publish_hour=settings.publish_hour_pkt)
    if label == "Last 5 days":
        return range_for_last_n(5, end=end)
    if label == "Last 30 days":
        return end - timedelta(days=30), end
    if label == "This month":
        return end.replace(day=1), end
    if label == "YTD":
        return date(end.year, 1, 1), end
    return date(end.year - YEARS_BACK, 1, 1), end  # Max


def render_download(settings, registry) -> None:
    state = get_state()
    snap = state.snapshot()
    running = snap["running"]

    st.subheader("Download")

    grouped = registry.by_section()
    left, right = st.columns([2, 1])

    with left:
        st.caption("Datasets")
        if "selected_keys" not in st.session_state:
            st.session_state.selected_keys = ["nd_accepted", "omts"]

        picked: list[str] = []
        for section, members in grouped.items():
            with st.expander(f"{section} ({len(members)})", expanded=section == "Off Market / NDM"):
                whole = st.checkbox(
                    f"Select all of {section}",
                    key=f"sec_{section}",
                    disabled=running,
                )
                for ds in members:
                    checked = st.checkbox(
                        f"{ds.label}  ·  `{ds.key}`  ·  {', '.join(ds.exts)}",
                        value=whole or ds.key in st.session_state.selected_keys,
                        key=f"ds_{ds.key}",
                        disabled=running,
                    )
                    if checked:
                        picked.append(ds.key)
        st.session_state.selected_keys = picked

    with right:
        st.caption("Date range")
        preset = st.selectbox(
            "Quick range",
            ["Custom", "Last 5 days", "Last 30 days", "This month", "YTD", "Max (10 years)"],
            disabled=running,
        )
        default_end = last_completed_trading_day(publish_hour=settings.publish_hour_pkt)
        default_start = default_end - timedelta(days=7)
        if preset != "Custom":
            default_start, default_end = quick_range(preset, settings)

        date_from = st.date_input("From", value=default_start, disabled=running, format="YYYY-MM-DD")
        date_to = st.date_input("To", value=default_end, disabled=running, format="YYYY-MM-DD")

        chosen = [registry.get(k) for k in picked]
        available_exts = sorted({e for ds in chosen for e in ds.exts})
        exts = st.multiselect(
            "Extensions (blank = all)",
            available_exts,
            disabled=running or not available_exts,
            help="Only matters for datasets published in more than one format.",
        )
        force = st.checkbox("Re-download files already stored", disabled=running)

    if not chosen:
        st.info("Pick at least one dataset to begin.")
        return

    units = jobs.plan_units(chosen, date_from, date_to, exts=exts or None)
    store = Store(settings.data_dir)
    with Manifest(store.manifest_path) as manifest:
        pending = jobs.pending_units(
            units, manifest=manifest, store=store, force=force, recheck_days=settings.recheck_days
        )
    est = jobs.estimate(len(pending), settings)

    a, b, c = st.columns(3)
    a.metric("Files planned", len(units))
    b.metric("To fetch", len(pending))
    c.metric("Estimated time", est.human_time)

    start_col, stop_col = st.columns(2)
    if start_col.button("Start", type="primary", disabled=running or not pending, use_container_width=True):
        start_job(state, chosen, date_from, date_to, settings, exts or None, force)
        st.rerun()
    if stop_col.button("Stop", disabled=not running, use_container_width=True):
        state.request_cancel()
        st.rerun()

    render_progress(state)


def render_progress(state: JobState) -> None:
    snap = state.snapshot()
    if not snap["running"] and snap["summary"] is None and snap["failure"] is None:
        return

    st.divider()
    planned = max(snap["planned"], 1)
    st.progress(min(snap["done"] / planned, 1.0), text=f"{snap['done']} / {snap['planned']} files")

    counts = snap["counts"]
    cols = st.columns(4)
    cols[0].metric("ok", counts.get(OK, 0))
    cols[1].metric("missing", counts.get(MISSING, 0))
    cols[2].metric("error", counts.get(ERROR, 0))
    cols[3].metric("skipped", counts.get(jobs.SKIPPED, 0))

    if snap["log"]:
        st.code("\n".join(snap["log"][:25]), language=None)

    if snap["failure"]:
        st.error(f"The job crashed: {snap['failure']}")
    elif snap["summary"] is not None:
        summary = snap["summary"]
        if summary.aborted:
            st.error(f"Stopped early: {summary.abort_reason}. Check the site before retrying.")
        elif summary.cancelled:
            st.warning("Cancelled. Press Start again to resume where it stopped.")
        else:
            st.success(f"Done — {summary.as_line()}")

    if snap["running"]:
        import time

        time.sleep(0.6)
        st.rerun()


# --- Coverage ---------------------------------------------------------------


def colour(value: str) -> str:
    if not value or value == "—":
        return "color:#bbb"
    if value.startswith("ok"):
        return "background-color:#1b5e20; color:#fff"
    if value.startswith("err"):
        return "background-color:#8b0000; color:#fff"
    return "background-color:#5d4037; color:#fff"  # missing only


def render_coverage(settings, registry) -> None:
    st.subheader("Coverage")
    st.caption("What the manifest recorded, by dataset and month. Green = files stored.")

    store = Store(settings.data_dir)
    if not store.manifest_path.exists():
        st.info("No manifest yet. Run a download first.")
        return

    with Manifest(store.manifest_path) as manifest:
        rows = manifest.coverage_rows()
        totals = manifest.counts_by_status()

    if not rows:
        st.info("The manifest is empty. Run a download first.")
        return

    cols = st.columns(3)
    cols[0].metric("ok", totals.get(OK, 0))
    cols[1].metric("missing", totals.get(MISSING, 0))
    cols[2].metric("error", totals.get(ERROR, 0))

    frame = pd.DataFrame(rows)
    pivot = frame.pivot_table(index="dataset", columns=["month"], values="n", aggfunc="sum", fill_value=0)

    def cell(dataset: str, month: str) -> str:
        subset = frame[(frame["dataset"] == dataset) & (frame["month"] == month)]
        if subset.empty:
            return "—"
        by_status = dict(zip(subset["status"], subset["n"]))
        ok = by_status.get(OK, 0)
        err = by_status.get(ERROR, 0)
        missing = by_status.get(MISSING, 0)
        if err:
            return f"err {err}"
        if ok:
            return f"ok {ok}"
        return f"miss {missing}"

    display = pd.DataFrame(
        {month: [cell(ds, month) for ds in pivot.index] for month in pivot.columns},
        index=pivot.index,
    )
    st.dataframe(display.style.map(colour), use_container_width=True)


# --- Other Downloads --------------------------------------------------------


def render_other(settings) -> None:
    st.subheader("Other Downloads")
    st.info(
        "Arrives in Phase 2.\n\n"
        "These files (`dailystockmkt.pdf`, `kse_index.lis.Z`, `Symbol_LotSize.zip` and the rest) "
        "have fixed names with no date in the URL, so they are latest-only and cannot be "
        "backfilled. The plan is a daily snapshot from now on, de-duplicated by SHA-256."
    )
    snapshots = Store(settings.data_dir).other_dir
    if snapshots.exists():
        found = sorted(p.name for p in snapshots.iterdir())
        st.write(f"{len(found)} snapshot folder(s) on disk.")


# --- Settings ---------------------------------------------------------------


def render_settings(settings, registry) -> None:
    st.subheader("Settings")
    st.caption("Read-only. Edit `config/settings.yaml` and restart to change these.")

    left, right = st.columns(2)
    with left:
        st.text_input("Data directory", str(settings.data_dir), disabled=True)
        st.text_input("Base URL", settings.base_url, disabled=True)
        st.number_input("Delay (seconds)", value=float(settings.delay_seconds), disabled=True)
        st.number_input("Jitter (seconds)", value=float(settings.jitter_seconds), disabled=True)
    with right:
        st.number_input("Timeout (seconds)", value=float(settings.timeout), disabled=True)
        st.number_input("Retries", value=int(settings.retries), disabled=True)
        st.number_input("Stop after N consecutive errors", value=int(settings.max_consecutive_errors), disabled=True)
        st.number_input("Re-check 'missing' within N days", value=int(settings.recheck_days), disabled=True)

    st.caption("Registry")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "key": d.key,
                    "label": d.label,
                    "section": d.section,
                    "extensions": ", ".join(d.exts),
                    "first available": d.first_available.isoformat() if d.first_available else "unknown",
                }
                for d in registry
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.caption(
        "Personal, non-commercial research use only. Do not redistribute PSX data or host this "
        "archive publicly (see CLAUDE.md, 'Legal & politeness')."
    )


# --- main -------------------------------------------------------------------


def main() -> None:
    st.title("PSX Data Downloader")

    try:
        settings, registry = load_config(str(DEFAULT_CONFIG_DIR))
    except (ConfigError, RegistryError) as exc:
        st.error(f"Configuration problem: {exc}")
        st.stop()
        return

    configure_logging(Store(settings.data_dir).logs_dir)

    download, coverage, other, config_tab = st.tabs(["Download", "Coverage", "Other Downloads", "Settings"])
    with download:
        render_download(settings, registry)
    with coverage:
        render_coverage(settings, registry)
    with other:
        render_other(settings)
    with config_tab:
        render_settings(settings, registry)


if __name__ == "__main__":
    # `streamlit run app.py` executes this file as __main__.
    main()
