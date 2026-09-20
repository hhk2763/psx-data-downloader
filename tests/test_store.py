"""Step 3 — paths, atomic writes, and the disk walk."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from psx.store import PART_SUFFIX, Store, sha256_bytes, sha256_file

THU = date(2026, 9, 17)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


# --- paths ------------------------------------------------------------------


def test_path_for_uses_the_documented_layout(store: Store) -> None:
    path = store.path_for("nd_accepted", THU, "pdf")
    assert path == store.raw_dir / "nd_accepted" / "2026" / "2026-09-17.pdf"


def test_path_for_is_built_with_pathlib_not_string_separators(store: Store) -> None:
    """The owner is on Windows; no '/' may be baked into a path component."""
    path = store.path_for("omts", THU, "csv")
    assert all("/" not in part and "\\" not in part for part in path.relative_to(store.raw_dir).parts)
    assert path.parts[-3:] == ("omts", "2026", "2026-09-17.csv")


def test_compressed_extension_survives_its_capital(store: Store) -> None:
    assert store.path_for("mkt_summary", THU, "Z").name == "2026-09-17.Z"


def test_year_directory_comes_from_the_trade_date(store: Store) -> None:
    assert store.path_for("omts", date(2015, 1, 2), "csv").parent.name == "2015"


def test_standard_locations(store: Store, tmp_path: Path) -> None:
    assert store.manifest_path == tmp_path / "data" / "manifest.db"
    assert store.logs_dir == tmp_path / "data" / "logs"
    assert store.other_dir == tmp_path / "data" / "raw" / "_other"


# --- writing ----------------------------------------------------------------


def test_write_creates_the_file_and_reports_size_and_hash(store: Store) -> None:
    data = b"%PDF-1.4 fake pdf bytes"
    result = store.write("nd_accepted", THU, "pdf", data)

    assert result.path.read_bytes() == data
    assert result.bytes == len(data)
    assert result.sha256 == hashlib.sha256(data).hexdigest()


def test_write_creates_missing_parent_directories(store: Store) -> None:
    assert not store.raw_dir.exists()
    store.write("omts", THU, "csv", b"a,b\n1,2\n")
    assert store.path_for("omts", THU, "csv").is_file()


def test_successful_write_leaves_no_part_file(store: Store) -> None:
    result = store.write("omts", THU, "csv", b"data")
    assert list(store.raw_dir.rglob("*" + PART_SUFFIX)) == []
    assert not result.path.with_name(result.path.name + PART_SUFFIX).exists()


def test_a_failed_write_leaves_neither_a_part_file_nor_a_target(store: Store, monkeypatch) -> None:
    """A crash mid-write must not leave a truncated archive file behind."""
    target = store.path_for("omts", THU, "csv")
    real_open = Path.open

    def failing_open(self, *args, **kwargs):  # noqa: ANN001
        handle = real_open(self, *args, **kwargs)
        if self.name.endswith(PART_SUFFIX):
            handle.close()
            raise OSError("disk full")
        return handle

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(OSError, match="disk full"):
        store.write("omts", THU, "csv", b"data")

    monkeypatch.undo()
    assert not target.exists()
    assert list(store.raw_dir.rglob("*" + PART_SUFFIX)) == []


def test_write_overwrites_an_existing_file_atomically(store: Store) -> None:
    store.write("omts", THU, "csv", b"old")
    result = store.write("omts", THU, "csv", b"new content")
    assert result.path.read_bytes() == b"new content"


def test_sweep_partials_removes_leftovers_from_a_killed_run(store: Store) -> None:
    store.write("omts", THU, "csv", b"good")
    stale = store.path_for("omts", date(2026, 9, 16), "csv")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.with_name(stale.name + PART_SUFFIX).write_bytes(b"half")

    assert store.sweep_partials() == 1
    assert list(store.raw_dir.rglob("*" + PART_SUFFIX)) == []
    assert store.exists("omts", THU, "csv")


def test_sweep_partials_on_an_empty_store_is_a_no_op(store: Store) -> None:
    assert store.sweep_partials() == 0


# --- existence --------------------------------------------------------------


def test_exists_is_false_before_any_download(store: Store) -> None:
    assert not store.exists("omts", THU, "csv")


def test_exists_is_false_for_a_zero_byte_file(store: Store) -> None:
    """A 0-byte file is a failed download, not a stored one."""
    path = store.path_for("omts", THU, "csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    assert path.is_file()
    assert not store.exists("omts", THU, "csv")


def test_exists_is_true_after_a_write(store: Store) -> None:
    store.write("omts", THU, "csv", b"x")
    assert store.exists("omts", THU, "csv")
    assert store.size_of("omts", THU, "csv") == 1


# --- hashing ----------------------------------------------------------------


def test_sha256_file_matches_sha256_bytes(store: Store) -> None:
    data = b"some bytes to hash" * 100
    result = store.write("omts", THU, "csv", data)
    assert sha256_file(result.path) == sha256_bytes(data) == hashlib.sha256(data).hexdigest()


# --- the disk walk ----------------------------------------------------------


def test_iter_existing_round_trips_written_files(store: Store) -> None:
    store.write("omts", THU, "csv", b"one")
    store.write("nd_accepted", date(2025, 3, 4), "pdf", b"two")
    store.write("mkt_summary", date(2015, 1, 2), "Z", b"three")

    found = {(f.dataset, f.trade_date, f.ext) for f in store.iter_existing()}
    assert found == {
        ("omts", THU, "csv"),
        ("nd_accepted", date(2025, 3, 4), "pdf"),
        ("mkt_summary", date(2015, 1, 2), "Z"),
    }


def test_iter_existing_reports_sizes_and_paths(store: Store) -> None:
    store.write("omts", THU, "csv", b"12345")
    found = list(store.iter_existing())
    assert len(found) == 1
    assert found[0].bytes == 5
    assert found[0].path == store.path_for("omts", THU, "csv")


def test_iter_existing_can_filter_to_one_dataset(store: Store) -> None:
    store.write("omts", THU, "csv", b"one")
    store.write("nd_accepted", THU, "pdf", b"two")
    assert [f.dataset for f in store.iter_existing(dataset="omts")] == ["omts"]


def test_iter_existing_skips_partials_other_downloads_and_junk(store: Store) -> None:
    store.write("omts", THU, "csv", b"keep me")

    partial = store.path_for("omts", date(2026, 9, 16), "csv")
    partial.with_name(partial.name + PART_SUFFIX).write_bytes(b"half")
    (store.raw_dir / "omts" / "2026" / "notes.txt").write_bytes(b"junk")
    (store.raw_dir / "omts" / "2026" / "2026-13-45.csv").write_bytes(b"impossible date")
    (store.raw_dir / "omts" / "scratch").mkdir(parents=True, exist_ok=True)
    (store.raw_dir / "omts" / "scratch" / "2026-09-17.csv").write_bytes(b"wrong level")
    other = store.other_dir / "kse_index"
    other.mkdir(parents=True, exist_ok=True)
    (other / "2026-09-17__kse_index.lis.Z").write_bytes(b"snapshot")

    found = list(store.iter_existing())
    assert [(f.dataset, f.trade_date, f.ext) for f in found] == [("omts", THU, "csv")]


def test_iter_existing_on_an_empty_store(store: Store) -> None:
    assert list(store.iter_existing()) == []
