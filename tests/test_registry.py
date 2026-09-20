"""Step 1 — registry loading and validation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from psx.registry import Dataset, RegistryError, load_registry
from tests.conftest import CONFIG_DIR


def write_registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(body, encoding="utf-8")
    return path


VALID = """
datasets:
  - key: omts
    label: Off Market Transaction Summary
    section: "Off Market / NDM"
    exts: ["csv"]
    first_available: null
  - key: fut_opn_int
    label: Symbol Wise Open Interest (DFC)
    section: Futures Market
    exts: ["pdf", "xls"]
    first_available: 2019-01-02
"""


# --- the registry we actually ship -----------------------------------------


def test_shipped_registry_has_all_24_datasets() -> None:
    registry = load_registry(CONFIG_DIR / "registry.yaml")
    assert len(registry) == 24


def test_shipped_registry_multi_extension_datasets() -> None:
    """CLAUDE.md names exactly three datasets with more than one extension."""
    registry = load_registry(CONFIG_DIR / "registry.yaml")
    multi = {d.key: d.exts for d in registry if len(d.exts) > 1}
    assert multi == {
        "reval_rates_gis": ("csv", "pdf"),
        "fut_opn_int": ("pdf", "xls"),
        "csf_opn_int": ("pdf", "xls"),
    }


def test_shipped_registry_priority_datasets_present() -> None:
    """The datasets the owner trades on must all resolve."""
    registry = load_registry(CONFIG_DIR / "registry.yaml")
    priority = ["nd_accepted", "nd_rejected", "omts", "omtpdf", "fut_opn_int", "dfc_nbs", "mkt_summary"]
    assert [d.key for d in registry.resolve(keys=priority)] == priority


def test_shipped_registry_ndm_section() -> None:
    registry = load_registry(CONFIG_DIR / "registry.yaml")
    assert [d.key for d in registry.in_section("Off Market / NDM")] == [
        "nd_accepted",
        "nd_rejected",
        "nd_threshold",
        "omts",
        "omtpdf",
    ]


def test_shipped_registry_first_available_is_unset() -> None:
    """Phase 1 ships no lower bounds; `probe` (Phase 2) fills them in."""
    registry = load_registry(CONFIG_DIR / "registry.yaml")
    assert all(d.first_available is None for d in registry)


# --- parsing ----------------------------------------------------------------


def test_loads_and_types_entries(tmp_path: Path) -> None:
    registry = load_registry(write_registry(tmp_path, VALID))
    omts = registry.get("omts")
    assert omts.label == "Off Market Transaction Summary"
    assert omts.section == "Off Market / NDM"
    assert omts.exts == ("csv",)
    assert omts.first_available is None
    assert registry.get("fut_opn_int").first_available == date(2019, 1, 2)


def test_url_path_matches_the_documented_pattern() -> None:
    ds = Dataset(key="nd_accepted", label="ND Accepted", section="Off Market / NDM", exts=("pdf",))
    assert ds.url_path(date(2026, 9, 17), "pdf") == "/download/nd_accepted/2026-09-17.pdf"


def test_covers_respects_first_available() -> None:
    ds = Dataset(
        key="omts",
        label="x",
        section="s",
        exts=("csv",),
        first_available=date(2019, 1, 2),
    )
    assert not ds.covers(date(2018, 12, 31))
    assert ds.covers(date(2019, 1, 2))
    assert ds.covers(date(2026, 9, 17))


def test_covers_is_true_when_first_available_is_unknown(omts: Dataset) -> None:
    assert omts.covers(date(2010, 1, 1))


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    section: S\n    exts: [csv]\n"
            "  - key: a\n    label: B\n    section: S\n    exts: [pdf]\n",
            "duplicate key",
            id="duplicate-key",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    section: S\n",
            "'exts' must be a non-empty list",
            id="missing-exts",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    section: S\n    exts: []\n",
            "'exts' must be a non-empty list",
            id="empty-exts",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    section: S\n    exts: [docx]\n",
            "unsupported extension",
            id="unknown-ext",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    exts: [csv]\n",
            "missing or empty 'section'",
            id="missing-section",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: ''\n    section: S\n    exts: [csv]\n",
            "missing or empty 'label'",
            id="empty-label",
        ),
        pytest.param(
            "datasets:\n  - key: 'Bad Key'\n    label: A\n    section: S\n    exts: [csv]\n",
            "lowercase letters",
            id="bad-key-chars",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    section: S\n    exts: [csv]\n    extra: 1\n",
            r"unknown field\(s\): extra",
            id="typo-field",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    section: S\n    exts: [csv, csv]\n",
            "duplicate extension",
            id="duplicate-ext",
        ),
        pytest.param(
            "datasets:\n  - key: a\n    label: A\n    section: S\n    exts: [csv]\n    first_available: nope\n",
            "not a YYYY-MM-DD date",
            id="bad-date",
        ),
        pytest.param("datasets: []\n", "non-empty list", id="empty-registry"),
        pytest.param("other: 1\n", "top-level 'datasets:' list", id="no-datasets-key"),
    ],
)
def test_invalid_registry_raises_with_a_useful_message(tmp_path: Path, body: str, expected: str) -> None:
    with pytest.raises(RegistryError, match=expected):
        load_registry(write_registry(tmp_path, body))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="registry not found"):
        load_registry(tmp_path / "nope.yaml")


# --- selection --------------------------------------------------------------


def test_resolve_by_keys(mini_registry) -> None:
    assert [d.key for d in mini_registry.resolve(keys=["omts", "fut_opn_int"])] == ["omts", "fut_opn_int"]


def test_resolve_by_section_is_case_insensitive(mini_registry) -> None:
    assert [d.key for d in mini_registry.resolve(section="off market / ndm")] == ["omts", "nd_accepted"]


def test_resolve_all(mini_registry) -> None:
    assert len(mini_registry.resolve(all_datasets=True)) == 3


def test_resolve_requires_exactly_one_selector(mini_registry) -> None:
    with pytest.raises(RegistryError, match="no dataset selected"):
        mini_registry.resolve()
    with pytest.raises(RegistryError, match="only one of"):
        mini_registry.resolve(keys=["omts"], all_datasets=True)


def test_unknown_key_and_section_name_the_alternatives(mini_registry) -> None:
    with pytest.raises(RegistryError, match="unknown dataset key"):
        mini_registry.resolve(keys=["nope"])
    with pytest.raises(RegistryError, match="unknown section"):
        mini_registry.resolve(section="Nope")


def test_by_section_and_sections_preserve_registry_order(mini_registry) -> None:
    assert mini_registry.sections() == ["Off Market / NDM", "Futures Market"]
    assert list(mini_registry.by_section()) == ["Off Market / NDM", "Futures Market"]
