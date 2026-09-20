"""Shared fixtures.

Everything here stays offline: no test may touch the network.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from psx.config import Settings  # noqa: E402
from psx.registry import Dataset, Registry  # noqa: E402

#: The real config directory, used by tests that assert on the shipped registry.
CONFIG_DIR = PROJECT_ROOT / "config"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Fast, offline settings pointed at a temp data dir."""
    return Settings(
        base_url="https://dps.psx.com.pk",
        data_dir=tmp_path / "data",
        delay_seconds=0.0,
        jitter_seconds=0.0,
        timeout=5.0,
        retries=3,
        backoff_seconds=0.0,
        max_consecutive_errors=10,
        recheck_days=3,
        publish_hour_pkt=19,
    )


@pytest.fixture
def omts() -> Dataset:
    return Dataset(key="omts", label="Off Market Transaction Summary", section="Off Market / NDM", exts=("csv",))


@pytest.fixture
def fut_opn_int() -> Dataset:
    """A two-extension dataset."""
    return Dataset(
        key="fut_opn_int",
        label="Symbol Wise Open Interest (DFC)",
        section="Futures Market",
        exts=("pdf", "xls"),
    )


@pytest.fixture
def mini_registry(omts: Dataset, fut_opn_int: Dataset) -> Registry:
    nd = Dataset(key="nd_accepted", label="ND Accepted", section="Off Market / NDM", exts=("pdf",))
    return Registry(datasets=(omts, nd, fut_opn_int))


@pytest.fixture
def monday() -> date:
    """2026-09-14 is a Monday; the week runs Mon 14 ... Fri 18, Sat 19, Sun 20."""
    return date(2026, 9, 14)
