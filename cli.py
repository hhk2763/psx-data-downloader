"""PSX Data Downloader — command line entry point.

    python cli.py fetch --datasets omts,nd_accepted --from 2024-01-01 --to 2026-09-17
    python cli.py fetch --section "Off Market / NDM" --from 2025-01-01
    python cli.py fetch --all --last 5
    python cli.py reconcile

Phase 2 adds: daily, snapshot-other, discover, probe, coverage.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from tqdm import tqdm

from psx import jobs
from psx.calendar import last_completed_trading_day, range_for_last_n
from psx.config import DEFAULT_CONFIG_DIR, ConfigError, configure_logging, load_settings
from psx.manifest import Manifest, entry_for_stored_file
from psx.registry import RegistryError, load_registry
from psx.store import Store, sha256_file

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Download raw daily files from the PSX Data Portal into a local archive.",
)


def _parse_date(value: str | None, label: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"{label} must be YYYY-MM-DD, got {value!r}") from None


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _load(config_dir: Path, data_dir: Path | None):
    """Settings plus registry, with friendly errors instead of tracebacks."""
    try:
        settings = load_settings(config_dir / "settings.yaml", data_dir=data_dir)
        registry = load_registry(config_dir / "registry.yaml")
    except (ConfigError, RegistryError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    return settings, registry


@app.command()
def fetch(
    datasets: Optional[str] = typer.Option(None, "--datasets", "-d", help="Comma-separated dataset keys."),
    section: Optional[str] = typer.Option(None, "--section", "-s", help='Whole section, e.g. "Off Market / NDM".'),
    all_datasets: bool = typer.Option(False, "--all", help="Every dataset in the registry."),
    date_from: Optional[str] = typer.Option(None, "--from", "-f", help="Start date, YYYY-MM-DD."),
    date_to: Optional[str] = typer.Option(None, "--to", "-t", help="End date. Defaults to the last completed trading day."),
    last: Optional[int] = typer.Option(None, "--last", "-n", min=1, help="The last N trading days instead of --from."),
    ext: Optional[str] = typer.Option(None, "--ext", "-e", help="Only these extensions, e.g. pdf or pdf,xls."),
    force: bool = typer.Option(False, "--force", help="Re-download files already recorded ok."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan and the estimate; make no requests."),
    config_dir: Path = typer.Option(DEFAULT_CONFIG_DIR, "--config", help="Directory holding the YAML config."),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", help="Override the archive location."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log every file, not just problems."),
) -> None:
    """Download daily files for a dataset selection and date range."""
    settings, registry = _load(config_dir, data_dir)

    selectors = [bool(datasets), bool(section), all_datasets]
    if sum(selectors) == 0:
        raise typer.BadParameter("choose --datasets, --section or --all")
    if sum(selectors) > 1:
        raise typer.BadParameter("choose only one of --datasets, --section or --all")

    try:
        chosen = registry.resolve(keys=_split_csv(datasets) or None, section=section, all_datasets=all_datasets)
    except RegistryError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    parsed_to = _parse_date(date_to, "--to")
    parsed_from = _parse_date(date_from, "--from")

    if last is not None:
        if parsed_from is not None:
            raise typer.BadParameter("use either --last or --from, not both")
        start, end = range_for_last_n(last, end=parsed_to, publish_hour=settings.publish_hour_pkt)
    else:
        if parsed_from is None:
            raise typer.BadParameter("--from is required (or use --last N)")
        start = parsed_from
        end = parsed_to or last_completed_trading_day(publish_hour=settings.publish_hour_pkt)

    if start > end:
        raise typer.BadParameter(f"--from ({start}) is after --to ({end})")

    exts = _split_csv(ext) or None
    store = Store(settings.data_dir)
    configure_logging(store.logs_dir, verbose=verbose)

    typer.echo(
        f"{len(chosen)} dataset(s), {start.isoformat()} .. {end.isoformat()}"
        + (f", extensions: {', '.join(exts)}" if exts else "")
    )

    units = jobs.plan_units(chosen, start, end, exts=exts)
    if not units:
        typer.echo("Nothing to do: the range contains no trading days for this selection.")
        raise typer.Exit(code=0)

    with Manifest(store.manifest_path) as manifest:
        pending = jobs.pending_units(
            units, manifest=manifest, store=store, force=force, recheck_days=settings.recheck_days
        )
        est = jobs.estimate(len(pending), settings)
        typer.echo(
            f"{len(units)} file(s) planned, {len(pending)} to fetch "
            f"({len(units) - len(pending)} already stored), about {est.human_time} at {settings.delay_seconds}s/request."
        )

        if dry_run:
            typer.echo("Dry run: no requests made.")
            raise typer.Exit(code=0)
        if not pending:
            typer.echo("Everything is already downloaded.")
            raise typer.Exit(code=0)

        bar = tqdm(total=len(units), unit="file", desc="fetching", leave=True)

        def progress(dataset_key: str, trade_date: date, file_ext: str, status: str) -> None:
            bar.update(1)
            bar.set_postfix_str(f"{dataset_key} {trade_date.isoformat()}.{file_ext} {status}", refresh=False)

        try:
            summary = jobs.run(
                chosen,
                start,
                end,
                settings=settings,
                exts=exts,
                force=force,
                progress_cb=progress,
                store=store,
                manifest=manifest,
            )
        finally:
            bar.close()

    typer.echo("")
    typer.echo(
        f"ok={summary.ok}  missing={summary.missing}  error={summary.error}  "
        f"skipped={summary.skipped_existing}  requests={summary.requests_made}  "
        f"{summary.bytes_downloaded / 1_048_576:.1f} MB  in {summary.elapsed_seconds:.0f}s"
    )

    if summary.aborted:
        typer.secho(f"Stopped early: {summary.abort_reason}.", fg=typer.colors.RED, err=True)
    if summary.cancelled:
        typer.secho("Cancelled. Re-run the same command to resume.", fg=typer.colors.YELLOW, err=True)
    for problem in summary.errors[:10]:
        typer.secho(f"  error: {problem}", fg=typer.colors.RED, err=True)
    if len(summary.errors) > 10:
        typer.secho(f"  ... and {len(summary.errors) - 10} more (see the log)", fg=typer.colors.RED, err=True)

    raise typer.Exit(code=0 if summary.succeeded else 1)


@app.command()
def reconcile(
    dataset: Optional[str] = typer.Option(None, "--dataset", help="Limit to one dataset key."),
    config_dir: Path = typer.Option(DEFAULT_CONFIG_DIR, "--config", help="Directory holding the YAML config."),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", help="Override the archive location."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Rebuild the manifest from the files on disk. The disk always wins."""
    settings, _ = _load(config_dir, data_dir)
    store = Store(settings.data_dir)
    configure_logging(store.logs_dir, verbose=verbose)

    if not store.raw_dir.exists():
        typer.echo(f"Nothing to reconcile: {store.raw_dir} does not exist.")
        raise typer.Exit(code=0)

    removed = store.sweep_partials()
    if removed:
        typer.echo(f"Removed {removed} leftover .part file(s).")

    entries = []
    scanned = 0
    empty = 0
    for found in tqdm(list(store.iter_existing(dataset=dataset)), unit="file", desc="hashing"):
        scanned += 1
        if found.bytes == 0:
            empty += 1
            continue
        entries.append(
            entry_for_stored_file(found.dataset, found.trade_date, found.ext, found.bytes, sha256_file(found.path))
        )

    with Manifest(store.manifest_path) as manifest:
        before = manifest.count()
        written = manifest.bulk_record(entries)
        after = manifest.count()

    typer.echo(
        f"Scanned {scanned} file(s); recorded {written} as ok "
        f"({after - before} new row(s), {written - (after - before)} updated)."
    )
    if empty:
        typer.secho(f"Skipped {empty} zero-byte file(s); re-run fetch to replace them.", fg=typer.colors.YELLOW)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        typer.secho("\nInterrupted.", fg=typer.colors.YELLOW, err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
