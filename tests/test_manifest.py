"""Step 4 — the SQLite manifest and the resume decision."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from psx.manifest import ERROR, MISSING, OK, LogEntry, Manifest, entry_for_stored_file

THU = date(2026, 9, 17)
TODAY = date(2026, 9, 18)


@pytest.fixture
def manifest(tmp_path: Path):
    with Manifest(tmp_path / "data" / "manifest.db") as m:
        yield m


def ok_entry(trade_date: date = THU, dataset: str = "omts", ext: str = "csv") -> LogEntry:
    return LogEntry(
        dataset=dataset,
        trade_date=trade_date,
        ext=ext,
        status=OK,
        http_code=200,
        bytes=1234,
        sha256="a" * 64,
    )


# --- schema -----------------------------------------------------------------


def test_a_fresh_database_creates_the_schema(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "manifest.db"
    with Manifest(db) as m:
        assert db.exists()
        cols = [r["name"] for r in m.conn.execute("PRAGMA table_info(fetch_log)")]
    assert cols == [
        "dataset",
        "trade_date",
        "ext",
        "status",
        "http_code",
        "bytes",
        "sha256",
        "attempts",
        "error_msg",
        "fetched_at",
    ]


def test_primary_key_is_dataset_date_ext(manifest: Manifest) -> None:
    pk = [r["name"] for r in manifest.conn.execute("PRAGMA table_info(fetch_log)") if r["pk"]]
    assert pk == ["dataset", "trade_date", "ext"]


def test_reopening_an_existing_database_keeps_the_rows(tmp_path: Path) -> None:
    db = tmp_path / "manifest.db"
    with Manifest(db) as m:
        m.record(ok_entry())
    with Manifest(db) as m:
        assert m.count() == 1


# --- recording --------------------------------------------------------------


def test_record_round_trips_every_field(manifest: Manifest) -> None:
    manifest.record(ok_entry())
    entry = manifest.get("omts", THU, "csv")
    assert entry is not None
    assert (entry.status, entry.http_code, entry.bytes, entry.sha256) == (OK, 200, 1234, "a" * 64)
    assert entry.trade_date == THU
    assert entry.attempts == 1


def test_get_returns_none_for_an_unknown_row(manifest: Manifest) -> None:
    assert manifest.get("omts", THU, "csv") is None


def test_re_recording_increments_attempts_without_duplicating(manifest: Manifest) -> None:
    err = LogEntry(dataset="omts", trade_date=THU, ext="csv", status=ERROR, http_code=500, error_msg="boom")
    manifest.record(err)
    manifest.record(err)
    manifest.record(ok_entry())

    entry = manifest.get("omts", THU, "csv")
    assert entry is not None
    assert entry.attempts == 3
    assert entry.status == OK  # the latest outcome wins
    assert entry.error_msg is None
    assert manifest.count() == 1


def test_rows_for_different_extensions_coexist(manifest: Manifest) -> None:
    manifest.record(ok_entry(dataset="fut_opn_int", ext="pdf"))
    manifest.record(ok_entry(dataset="fut_opn_int", ext="xls"))
    assert manifest.count() == 2


def test_fetched_at_is_a_utc_iso_timestamp(manifest: Manifest) -> None:
    manifest.record(ok_entry())
    entry = manifest.get("omts", THU, "csv")
    assert entry is not None
    parsed = datetime.fromisoformat(entry.fetched_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)


def test_bulk_record_writes_everything(manifest: Manifest) -> None:
    entries = [ok_entry(trade_date=date(2026, 9, d)) for d in (14, 15, 16, 17)]
    assert manifest.bulk_record(entries) == 4
    assert manifest.count(OK) == 4


def test_bulk_record_of_nothing_is_a_no_op(manifest: Manifest) -> None:
    assert manifest.bulk_record([]) == 0


# --- the resume decision ----------------------------------------------------


def seed(manifest: Manifest, status: str, trade_date: date = THU, **kw) -> None:
    manifest.record(LogEntry(dataset="omts", trade_date=trade_date, ext="csv", status=status, **kw))


@pytest.mark.parametrize(
    ("status", "trade_date", "expected"),
    [
        pytest.param(None, THU, True, id="no-row-fetch"),
        pytest.param(OK, THU, False, id="ok-skip"),
        pytest.param(ERROR, THU, True, id="error-retry"),
        pytest.param(MISSING, TODAY, True, id="missing-today-recheck"),
        pytest.param(MISSING, date(2026, 9, 15), True, id="missing-3-days-ago-recheck"),
        pytest.param(MISSING, date(2026, 9, 14), False, id="missing-4-days-ago-skip"),
        pytest.param(MISSING, date(2020, 1, 6), False, id="missing-years-ago-skip"),
    ],
)
def test_should_fetch_matrix(manifest: Manifest, status, trade_date: date, expected: bool) -> None:
    if status is not None:
        seed(manifest, status, trade_date)
    assert manifest.should_fetch("omts", trade_date, "csv", recheck_days=3, today=TODAY) is expected


@pytest.mark.parametrize("status", [OK, MISSING, ERROR])
def test_force_always_fetches(manifest: Manifest, status: str) -> None:
    seed(manifest, status, date(2020, 1, 6))
    assert manifest.should_fetch("omts", date(2020, 1, 6), "csv", force=True, recheck_days=3, today=TODAY)


def test_recheck_window_is_configurable(manifest: Manifest) -> None:
    old = date(2026, 9, 10)
    seed(manifest, MISSING, old)
    assert not manifest.should_fetch("omts", old, "csv", recheck_days=3, today=TODAY)
    assert manifest.should_fetch("omts", old, "csv", recheck_days=30, today=TODAY)


def test_should_fetch_is_per_extension(manifest: Manifest) -> None:
    manifest.record(ok_entry(dataset="fut_opn_int", ext="pdf"))
    assert not manifest.should_fetch("fut_opn_int", THU, "pdf", today=TODAY)
    assert manifest.should_fetch("fut_opn_int", THU, "xls", today=TODAY)


# --- aggregates and maintenance ---------------------------------------------


def test_counts_by_status(manifest: Manifest) -> None:
    manifest.record(ok_entry(trade_date=date(2026, 9, 14)))
    manifest.record(ok_entry(trade_date=date(2026, 9, 15)))
    seed(manifest, MISSING, date(2026, 9, 16))
    seed(manifest, ERROR, THU)

    assert manifest.counts_by_status() == {OK: 2, MISSING: 1, ERROR: 1}
    assert manifest.count(OK) == 2


def test_coverage_rows_group_by_dataset_and_month(manifest: Manifest) -> None:
    manifest.record(ok_entry(trade_date=date(2026, 8, 3)))
    manifest.record(ok_entry(trade_date=date(2026, 9, 14)))
    manifest.record(ok_entry(trade_date=date(2026, 9, 15)))

    rows = manifest.coverage_rows()
    assert {"dataset": "omts", "month": "2026-09", "status": OK, "n": 2} in rows
    assert {"dataset": "omts", "month": "2026-08", "status": OK, "n": 1} in rows


def test_iter_entries_is_ordered_and_filterable(manifest: Manifest) -> None:
    manifest.record(ok_entry(dataset="omts"))
    manifest.record(ok_entry(dataset="nd_accepted", ext="pdf"))
    assert [e.dataset for e in manifest.iter_entries()] == ["nd_accepted", "omts"]
    assert [e.dataset for e in manifest.iter_entries(dataset="omts")] == ["omts"]


def test_delete_all_empties_the_log(manifest: Manifest) -> None:
    manifest.record(ok_entry())
    assert manifest.delete_all() == 1
    assert manifest.count() == 0


def test_entry_for_stored_file_builds_an_ok_row() -> None:
    entry = entry_for_stored_file("omts", THU, "csv", 99, "b" * 64)
    assert entry.status == OK
    assert entry.http_code == 200
    assert entry.bytes == 99
    assert entry.fetched_at
