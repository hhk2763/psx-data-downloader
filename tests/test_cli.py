"""Step 7 — argument handling, dry runs, and reconcile.

``jobs.run`` is stubbed out in most of these: the point is what the CLI asks
for, not what the network answers.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import cli as cli_module
from psx import jobs
from psx.jobs import JobSummary
from psx.manifest import Manifest
from psx.store import Store
from tests.conftest import CONFIG_DIR

runner = CliRunner()

THU = date(2026, 9, 17)
FRI = date(2026, 9, 18)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A config dir pointing at a throwaway data dir, so nothing real is touched."""
    config = tmp_path / "config"
    config.mkdir()
    settings = yaml.safe_load((CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"))
    settings["data_dir"] = str(tmp_path / "data")
    settings["delay_seconds"] = 0.0
    settings["jitter_seconds"] = 0.0
    (config / "settings.yaml").write_text(yaml.safe_dump(settings), encoding="utf-8")
    (config / "registry.yaml").write_text((CONFIG_DIR / "registry.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return config


@pytest.fixture
def captured(monkeypatch) -> dict:
    """Intercept jobs.run so no request can be attempted from a CLI test."""
    seen: dict = {}

    def fake_run(datasets, date_from, date_to, **kwargs):
        seen["datasets"] = [d.key for d in datasets]
        seen["date_from"] = date_from
        seen["date_to"] = date_to
        seen["exts"] = kwargs.get("exts")
        seen["force"] = kwargs.get("force")
        return JobSummary(planned=1, ok=1, requests_made=1)

    monkeypatch.setattr(cli_module.jobs, "run", fake_run)
    return seen


def invoke(workspace: Path, *args: str):
    return runner.invoke(cli_module.app, [*args, "--config", str(workspace)])


ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
BOX = re.compile(r"[─-╿]")


def plain(result) -> str:
    """Output with colour codes, box drawing and Rich's line wrapping removed.

    Typer renders usage errors inside a Rich panel whose width follows the
    terminal, so asserting on the raw text is width-dependent and flaky.
    """
    text = ANSI.sub("", result.output)
    text = BOX.sub(" ", text)
    return " ".join(text.split())


# --- dataset selection ------------------------------------------------------


def test_datasets_option_resolves_keys(workspace: Path, captured: dict) -> None:
    result = invoke(workspace, "fetch", "--datasets", "omts,nd_accepted", "--from", "2026-09-17")
    assert result.exit_code == 0
    assert captured["datasets"] == ["omts", "nd_accepted"]


def test_datasets_option_tolerates_spaces(workspace: Path, captured: dict) -> None:
    invoke(workspace, "fetch", "--datasets", "omts, nd_accepted ", "--from", "2026-09-17")
    assert captured["datasets"] == ["omts", "nd_accepted"]


def test_section_option_resolves_the_whole_section(workspace: Path, captured: dict) -> None:
    invoke(workspace, "fetch", "--section", "Off Market / NDM", "--from", "2026-09-17")
    assert captured["datasets"] == ["nd_accepted", "nd_rejected", "nd_threshold", "omts", "omtpdf"]


def test_all_selects_every_dataset(workspace: Path, captured: dict) -> None:
    invoke(workspace, "fetch", "--all", "--from", "2026-09-17")
    assert len(captured["datasets"]) == 24


def test_two_selectors_is_a_usage_error(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--datasets", "omts", "--all", "--from", "2026-09-17")
    assert result.exit_code != 0
    assert "only one of" in plain(result)


def test_no_selector_is_a_usage_error(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--from", "2026-09-17")
    assert result.exit_code != 0
    assert "choose --datasets" in plain(result)


def test_an_unknown_dataset_key_is_reported_clearly(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--datasets", "not_a_dataset", "--from", "2026-09-17")
    assert result.exit_code == 2
    assert "unknown dataset key" in plain(result)


def test_an_unknown_section_is_reported_clearly(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--section", "Nope", "--from", "2026-09-17")
    assert result.exit_code == 2
    assert "unknown section" in plain(result)


# --- date handling ----------------------------------------------------------


def test_last_n_computes_the_range(workspace: Path, captured: dict) -> None:
    invoke(workspace, "fetch", "--datasets", "omts", "--last", "5", "--to", "2026-09-18")
    assert captured["date_to"] == FRI
    assert captured["date_from"] == date(2026, 9, 14)


def test_to_defaults_to_the_last_completed_trading_day(workspace: Path, captured: dict) -> None:
    invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-01")
    assert captured["date_to"] <= date.today()
    assert captured["date_to"].weekday() < 5


def test_last_and_from_together_is_an_error(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--datasets", "omts", "--last", "5", "--from", "2026-09-01")
    assert result.exit_code != 0
    assert "either --last or --from" in plain(result)


def test_from_is_required_without_last(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--datasets", "omts")
    assert result.exit_code != 0
    assert "--from is required" in plain(result)


def test_an_inverted_range_is_rejected(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-18", "--to", "2026-09-01")
    assert result.exit_code != 0
    assert "is after" in plain(result)


def test_a_malformed_date_is_rejected(workspace: Path) -> None:
    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "18-09-2026")
    assert result.exit_code != 0
    assert "must be YYYY-MM-DD" in plain(result)


def test_a_weekend_only_range_does_nothing(workspace: Path, captured: dict) -> None:
    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-19", "--to", "2026-09-20")
    assert result.exit_code == 0
    assert "no trading days" in plain(result)
    assert captured == {}


# --- extensions and force ---------------------------------------------------


def test_ext_filter_is_passed_through(workspace: Path, captured: dict) -> None:
    invoke(workspace, "fetch", "--datasets", "fut_opn_int", "--from", "2026-09-17", "--ext", "pdf,xls")
    assert captured["exts"] == ["pdf", "xls"]


def test_force_is_passed_through(workspace: Path, captured: dict) -> None:
    invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-17", "--force")
    assert captured["force"] is True


# --- dry run ----------------------------------------------------------------


def test_dry_run_makes_no_requests_and_shows_an_estimate(workspace: Path, monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("a dry run must not fetch anything")

    monkeypatch.setattr(cli_module.jobs, "run", explode)

    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-14", "--to", "2026-09-18", "--dry-run")

    assert result.exit_code == 0
    assert "5 file(s) planned" in plain(result)
    assert "Dry run: no requests made." in plain(result)


def test_dry_run_counts_multi_extension_datasets_per_file(workspace: Path) -> None:
    result = invoke(
        workspace, "fetch", "--datasets", "fut_opn_int", "--from", "2026-09-17", "--to", "2026-09-17", "--dry-run"
    )
    assert "2 file(s) planned" in plain(result)


# --- exit codes -------------------------------------------------------------


def test_a_clean_run_exits_zero(workspace: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_module.jobs, "run", lambda *a, **k: JobSummary(ok=3, requests_made=3))
    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-17")
    assert result.exit_code == 0
    assert "ok=3" in plain(result)


def test_errors_exit_nonzero_and_are_listed(workspace: Path, monkeypatch) -> None:
    summary = JobSummary(ok=1, error=2, requests_made=3, errors=["omts 2026-09-17 .csv: HTTP 500"])
    monkeypatch.setattr(cli_module.jobs, "run", lambda *a, **k: summary)

    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-17")

    assert result.exit_code == 1
    assert "error=2" in plain(result)
    assert "HTTP 500" in plain(result)


def test_an_aborted_run_says_why(workspace: Path, monkeypatch) -> None:
    summary = JobSummary(error=10, aborted=True, abort_reason="10 consecutive errors")
    monkeypatch.setattr(cli_module.jobs, "run", lambda *a, **k: summary)

    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-17")

    assert result.exit_code == 1
    assert "Stopped early: 10 consecutive errors" in plain(result)


def test_a_cancelled_run_tells_the_user_how_to_resume(workspace: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_module.jobs, "run", lambda *a, **k: JobSummary(ok=2, cancelled=True))
    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-17")
    assert result.exit_code == 1
    assert "resume" in plain(result)


def test_a_broken_config_exits_two(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text("delay_seconds: 1.5\n", encoding="utf-8")
    (config / "registry.yaml").write_text("datasets: []\n", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["fetch", "--all", "--from", "2026-09-17", "--config", str(config)])

    assert result.exit_code == 2
    assert "non-empty list" in plain(result)


# --- resume through the CLI -------------------------------------------------


def test_a_second_invocation_skips_what_is_stored(workspace: Path, monkeypatch, tmp_path: Path) -> None:
    """End to end through the CLI, with the fetcher stubbed at the jobs layer."""
    store = Store(tmp_path / "data")
    store.write("omts", THU, "csv", b"already here")
    with Manifest(store.manifest_path) as m:
        from psx.manifest import entry_for_stored_file
        from psx.store import sha256_file

        m.record(entry_for_stored_file("omts", THU, "csv", 12, sha256_file(store.path_for("omts", THU, "csv"))))

    def explode(*args, **kwargs):
        raise AssertionError("nothing should be fetched")

    monkeypatch.setattr(cli_module.jobs, "run", explode)

    result = invoke(workspace, "fetch", "--datasets", "omts", "--from", "2026-09-17", "--to", "2026-09-17")

    assert result.exit_code == 0
    assert "Everything is already downloaded." in plain(result)


# --- reconcile --------------------------------------------------------------


def test_reconcile_rebuilds_the_manifest_from_disk(workspace: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    store.write("omts", THU, "csv", b"a,b\n1,2\n")
    store.write("nd_accepted", THU, "pdf", b"%PDF fake")
    store.write("mkt_summary", date(2015, 1, 2), "Z", b"compressed")

    result = invoke(workspace, "reconcile")

    assert result.exit_code == 0
    assert "Scanned 3 file(s); recorded 3 as ok" in plain(result)

    with Manifest(store.manifest_path) as m:
        assert m.count() == 3
        entry = m.get("omts", THU, "csv")
        assert entry is not None
        assert entry.status == "ok"
        assert entry.bytes == 8
        assert entry.sha256


def test_reconcile_makes_a_deleted_manifest_recoverable(workspace: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    store.write("omts", THU, "csv", b"data")
    invoke(workspace, "reconcile")
    store.manifest_path.unlink()

    result = invoke(workspace, "reconcile")

    assert result.exit_code == 0
    with Manifest(store.manifest_path) as m:
        assert m.count() == 1


def test_reconcile_skips_zero_byte_files_and_says_so(workspace: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    store.write("omts", THU, "csv", b"good")
    empty = store.path_for("omts", FRI, "csv")
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.touch()

    result = invoke(workspace, "reconcile")

    assert "recorded 1 as ok" in plain(result)
    assert "Skipped 1 zero-byte file(s)" in plain(result)


def test_reconcile_removes_leftover_partials(workspace: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    store.write("omts", THU, "csv", b"good")
    partial = store.path_for("omts", FRI, "csv")
    partial.with_name(partial.name + ".part").write_bytes(b"half")

    result = invoke(workspace, "reconcile")

    assert "Removed 1 leftover .part file(s)" in plain(result)
    assert list(store.raw_dir.rglob("*.part")) == []


def test_reconcile_can_be_limited_to_one_dataset(workspace: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    store.write("omts", THU, "csv", b"one")
    store.write("nd_accepted", THU, "pdf", b"two")

    invoke(workspace, "reconcile", "--dataset", "omts")

    with Manifest(store.manifest_path) as m:
        assert [e.dataset for e in m.iter_entries()] == ["omts"]


def test_reconcile_on_an_empty_archive(workspace: Path) -> None:
    result = invoke(workspace, "reconcile")
    assert result.exit_code == 0
    assert "Nothing to reconcile" in plain(result)
