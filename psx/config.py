"""Settings loading and logging setup.

Keeps YAML parsing in one place so nothing else in the package has to know the
file format. See ``config/settings.yaml`` for the documented defaults.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(ValueError):
    """Raised when settings.yaml is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration. Immutable once loaded."""

    base_url: str = "https://dps.psx.com.pk"
    data_dir: Path = PROJECT_ROOT / "data"
    delay_seconds: float = 1.5
    jitter_seconds: float = 0.5
    timeout: float = 30.0
    retries: int = 3
    backoff_seconds: float = 2.0
    max_consecutive_errors: int = 10
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    )
    recheck_days: int = 3
    publish_hour_pkt: int = 19

    def download_url(self, key: str, trade_date: date, ext: str) -> str:
        """Full URL for one daily file."""
        return f"{self.base_url.rstrip('/')}/download/{key}/{trade_date.isoformat()}.{ext}"


def load_settings(
    path: Path | str | None = None,
    *,
    data_dir: Path | str | None = None,
) -> Settings:
    """Load ``settings.yaml``. Missing keys fall back to the dataclass defaults.

    ``data_dir`` overrides the file (used by ``--data-dir`` and by tests).
    """
    path = Path(path) if path is not None else DEFAULT_CONFIG_DIR / "settings.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path}: expected a mapping at the top level")
        raw = loaded

    known = {f for f in Settings.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"{path}: unknown setting(s): {', '.join(sorted(unknown))}")

    if data_dir is not None:
        raw["data_dir"] = data_dir

    if "data_dir" in raw:
        d = Path(raw["data_dir"])
        raw["data_dir"] = d if d.is_absolute() else (PROJECT_ROOT / d)

    if "user_agent" in raw:
        raw["user_agent"] = " ".join(str(raw["user_agent"]).split())

    try:
        settings = Settings(**raw)
    except TypeError as exc:  # pragma: no cover - guarded by the unknown check
        raise ConfigError(f"{path}: {exc}") from exc

    if settings.delay_seconds < 0 or settings.jitter_seconds < 0:
        raise ConfigError(f"{path}: delay_seconds and jitter_seconds must be >= 0")
    if settings.retries < 1:
        raise ConfigError(f"{path}: retries must be >= 1")
    if settings.max_consecutive_errors < 1:
        raise ConfigError(f"{path}: max_consecutive_errors must be >= 1")
    if not 0 <= settings.publish_hour_pkt <= 23:
        raise ConfigError(f"{path}: publish_hour_pkt must be 0..23")
    return settings


def configure_logging(log_dir: Path, *, verbose: bool = False, today: date | None = None) -> None:
    """Log to ``log_dir/YYYY-MM-DD.log`` and to the console.

    Safe to call more than once: existing handlers are replaced.
    """
    from psx.calendar import today_pkt

    day = today or today_pkt()
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("psx")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_dir / f"{day.isoformat()}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(message)s"))
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(console)
