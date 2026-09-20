"""Load and validate ``config/registry.yaml`` into ``Dataset`` objects.

The registry is the only place datasets are declared. Adding a dataset is a
YAML edit, never a code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

import yaml

from psx.config import DEFAULT_CONFIG_DIR

#: Extensions seen on the PSX portal. A new one here is a deliberate decision,
#: not a typo in registry.yaml.
ALLOWED_EXTS = frozenset({"csv", "pdf", "xls", "xlsx", "zip", "Z", "lis", "txt", "lst"})

_KEY_RE = re.compile(r"^[a-z0-9_]+$")
_ENTRY_FIELDS = frozenset({"key", "label", "section", "exts", "first_available"})


class RegistryError(ValueError):
    """Raised when registry.yaml is malformed or a selection cannot be resolved."""


@dataclass(frozen=True, slots=True)
class Dataset:
    """One daily, date-keyed dataset."""

    key: str
    label: str
    section: str
    exts: tuple[str, ...]
    first_available: date | None = None

    def url_path(self, trade_date: date, ext: str) -> str:
        """Site-relative path for one file, e.g. ``/download/omts/2026-09-17.csv``."""
        return f"/download/{self.key}/{trade_date.isoformat()}.{ext}"

    def covers(self, trade_date: date) -> bool:
        """False for dates known to predate the dataset (see ``first_available``)."""
        return self.first_available is None or trade_date >= self.first_available


@dataclass(frozen=True, slots=True)
class Registry:
    """An ordered, validated collection of datasets."""

    datasets: tuple[Dataset, ...]

    def __iter__(self) -> Iterator[Dataset]:
        return iter(self.datasets)

    def __len__(self) -> int:
        return len(self.datasets)

    def __contains__(self, key: object) -> bool:
        return any(d.key == key for d in self.datasets)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.datasets)

    def get(self, key: str) -> Dataset:
        for d in self.datasets:
            if d.key == key:
                return d
        raise RegistryError(f"unknown dataset key: {key!r}. Known keys: {', '.join(self.keys)}")

    def sections(self) -> list[str]:
        """Section names in registry order, de-duplicated."""
        seen: list[str] = []
        for d in self.datasets:
            if d.section not in seen:
                seen.append(d.section)
        return seen

    def by_section(self) -> dict[str, list[Dataset]]:
        grouped: dict[str, list[Dataset]] = {}
        for d in self.datasets:
            grouped.setdefault(d.section, []).append(d)
        return grouped

    def in_section(self, section: str) -> list[Dataset]:
        matches = [d for d in self.datasets if d.section.casefold() == section.casefold()]
        if not matches:
            known = ", ".join(repr(s) for s in self.sections())
            raise RegistryError(f"unknown section: {section!r}. Known sections: {known}")
        return matches

    def resolve(
        self,
        *,
        keys: Sequence[str] | None = None,
        section: str | None = None,
        all_datasets: bool = False,
    ) -> list[Dataset]:
        """Turn a CLI/GUI selection into datasets. Exactly one selector applies.

        Both ``cli.py`` and ``app.py`` go through here so selection behaves
        identically in each.
        """
        chosen = [bool(keys), bool(section), bool(all_datasets)]
        if sum(chosen) == 0:
            raise RegistryError("no dataset selected: pass keys, a section, or all_datasets")
        if sum(chosen) > 1:
            raise RegistryError("choose only one of: keys, section, all_datasets")

        if all_datasets:
            return list(self.datasets)
        if section is not None:
            return self.in_section(section)
        assert keys is not None  # narrowed by the checks above
        return [self.get(k) for k in keys]


def _fail(index: int, key: Any, msg: str) -> None:
    where = f"entry {index}" + (f" ({key!r})" if key else "")
    raise RegistryError(f"registry.yaml: {where}: {msg}")


def _parse_first_available(value: Any, index: int, key: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            _fail(index, key, f"first_available is not a YYYY-MM-DD date: {value!r}")
    _fail(index, key, f"first_available must be a date or null, got {type(value).__name__}")
    return None  # pragma: no cover - _fail always raises


def _parse_entry(raw: Any, index: int, seen: set[str]) -> Dataset:
    if not isinstance(raw, dict):
        _fail(index, None, f"expected a mapping, got {type(raw).__name__}")

    key = raw.get("key")
    unknown = set(raw) - _ENTRY_FIELDS
    if unknown:
        _fail(index, key, f"unknown field(s): {', '.join(sorted(unknown))}")

    if not isinstance(key, str) or not key.strip():
        _fail(index, None, "missing or empty 'key'")
    if not _KEY_RE.match(key):
        _fail(index, key, "key must be lowercase letters, digits or underscores")
    if key in seen:
        _fail(index, key, "duplicate key")

    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        _fail(index, key, "missing or empty 'label'")

    section = raw.get("section")
    if not isinstance(section, str) or not section.strip():
        _fail(index, key, "missing or empty 'section'")

    exts = raw.get("exts")
    if not isinstance(exts, list) or not exts:
        _fail(index, key, "'exts' must be a non-empty list")
    cleaned: list[str] = []
    for ext in exts:
        if not isinstance(ext, str) or not ext.strip():
            _fail(index, key, f"bad extension: {ext!r}")
        ext = ext.strip().lstrip(".")
        if ext not in ALLOWED_EXTS:
            _fail(
                index,
                key,
                f"unsupported extension {ext!r}; allowed: {', '.join(sorted(ALLOWED_EXTS))}",
            )
        if ext in cleaned:
            _fail(index, key, f"duplicate extension {ext!r}")
        cleaned.append(ext)

    first_available = _parse_first_available(raw.get("first_available"), index, key)

    seen.add(key)
    return Dataset(
        key=key,
        label=label.strip(),
        section=section.strip(),
        exts=tuple(cleaned),
        first_available=first_available,
    )


def load_registry(path: Path | str | None = None) -> Registry:
    """Read, validate and return the registry."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_DIR / "registry.yaml"
    if not path.exists():
        raise RegistryError(f"registry not found: {path}")

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or "datasets" not in loaded:
        raise RegistryError(f"{path}: expected a top-level 'datasets:' list")
    entries = loaded["datasets"]
    if not isinstance(entries, list) or not entries:
        raise RegistryError(f"{path}: 'datasets' must be a non-empty list")

    seen: set[str] = set()
    datasets = tuple(_parse_entry(raw, i, seen) for i, raw in enumerate(entries))
    return Registry(datasets=datasets)
