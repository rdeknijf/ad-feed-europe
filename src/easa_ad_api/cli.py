"""Command line entry point."""

import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from easa_ad_api import ingest, store
from easa_ad_api.config import get_settings


def _conn() -> sqlite3.Connection:
    """Open the database and close it when the command ends."""
    conn = store.connect(get_settings().database_path)
    click.get_current_context().call_on_close(conn.close)
    return conn


def _fetcher() -> ingest.Fetcher:
    return ingest.Fetcher(delay=get_settings().request_delay_seconds)


def _raw_dir() -> Path:
    return get_settings().private_dir / "raw"


def _file_time(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat()


def _echo(result: ingest.IngestResult) -> None:
    click.echo(
        f"{result.source}: {result.rows} rows, {result.new} new, {result.changed} changed,"
        f" {result.events} events. {result.message}".rstrip()
    )


@click.group()
def main() -> None:
    """Unofficial mirror of EASA's published airworthiness directive list."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@main.command("ingest-archive")
@click.option("--from-file", type=click.Path(exists=True, path_type=Path), help="Saved archive page.")
def ingest_archive_cmd(from_file: Path | None) -> None:
    """Load the frozen archive page (Drupal settings JSON). One 8.4 MB request when live."""
    conn = _conn()
    if from_file:
        _echo(ingest.ingest_archive(conn, from_file.read_text(), f"file:{from_file}", _file_time(from_file)))
    else:
        _echo(ingest.fetch_archive(conn, _fetcher(), _raw_dir()))


@main.command("ingest-interim")
@click.option("--from-file", type=click.Path(exists=True, path_type=Path), help="Saved interim page.")
def ingest_interim_cmd(from_file: Path | None) -> None:
    """Load the interim HTML table and diff it by number and content hash."""
    conn = _conn()
    if from_file:
        _echo(ingest.ingest_interim(conn, from_file.read_text(), f"file:{from_file}", _file_time(from_file)))
    else:
        _echo(ingest.fetch_interim(conn, _fetcher(), _raw_dir()))


@main.command("ingest-csv")
@click.option("--period", type=int, help="Biweekly period number. Default: newest not yet loaded.")
@click.option("--from-file", type=click.Path(exists=True, path_type=Path), help="Saved CSV zip.")
def ingest_csv_cmd(period: int | None, from_file: Path | None) -> None:
    """Load a biweekly CSV zip and report numbers that the interim table lacks."""
    conn = _conn()
    if from_file:
        if period is None:
            raise click.UsageError("--period is required with --from-file")
        _echo(ingest.ingest_csv(conn, from_file.read_bytes(), period, f"file:{from_file}"))
        return
    if period is None:
        row = ingest.next_csv_period(conn)
    else:
        row = conn.execute("SELECT * FROM csv_period WHERE period=?", (period,)).fetchone()
    if row is None:
        raise click.ClickException("no CSV period to load; run ingest-interim first")
    _echo(ingest.fetch_csv(conn, _fetcher(), row["period"], row["csv_url"], _raw_dir()))


@main.command("fetch-pdfs")
@click.option("--limit", default=20, show_default=True, help="Maximum PDFs to fetch.")
@click.option("--include-archive", is_flag=True, help="Also fetch PDFs of archive-only rows.")
@click.option("--dry-run", is_flag=True, help="List the PDFs that would be fetched.")
def fetch_pdfs_cmd(limit: int, include_archive: bool, dry_run: bool) -> None:
    """Fetch new or changed EASA AD PDFs, keep their text privately, extract header fields."""
    conn = _conn()
    candidates = ingest.pdf_candidates(conn, include_archive=include_archive)[:limit]
    if dry_run:
        for c in candidates:
            click.echo(f"{c.number}\t{c.url}")
        click.echo(f"{len(candidates)} PDFs would be fetched")
        return
    _echo(ingest.fetch_pdfs(conn, _fetcher(), get_settings().private_dir, candidates))


@main.command("reparse-pdfs")
def reparse_pdfs_cmd() -> None:
    """Re-run the PDF header parser over the stored texts. No network."""
    click.echo(f"{ingest.reparse_pdfs(_conn(), get_settings().private_dir)} texts parsed")


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8602, show_default=True)
def serve(host: str, port: int) -> None:
    """Run the API with uvicorn."""
    import uvicorn

    uvicorn.run("easa_ad_api.api:app", host=host, port=port, proxy_headers=True)


def _due(conn: sqlite3.Connection, source: str, every: timedelta) -> bool:
    row = conn.execute("SELECT MAX(started_at) AS t FROM sync_run WHERE source=?", (source,)).fetchone()
    if not row["t"]:
        return True
    return datetime.fromisoformat(row["t"]) + every <= datetime.now(UTC)


@main.command()
@click.option("--pdf-limit", default=10, show_default=True)
def worker(pdf_limit: int) -> None:
    """Scheduled polling: interim every 30 minutes, archive weekly, new CSV when listed.

    Stops all live calls for 6 hours after HTTP 403 or 429.
    """
    conn = _conn()
    fetcher = _fetcher()
    log = logging.getLogger("worker")
    while True:
        try:
            if _due(conn, "interim", timedelta(minutes=30)):
                ingest.fetch_interim(conn, fetcher, _raw_dir())
                candidates = ingest.pdf_candidates(conn)[:pdf_limit]
                if candidates:
                    ingest.fetch_pdfs(conn, fetcher, get_settings().private_dir, candidates)
            if _due(conn, "archive", timedelta(days=7)):
                ingest.fetch_archive(conn, fetcher, _raw_dir())
            row = ingest.next_csv_period(conn)
            if row is not None:
                ingest.fetch_csv(conn, fetcher, row["period"], row["csv_url"], _raw_dir())
        except ingest.BlockedError:
            log.exception("blocked by source; pausing live calls for 6 hours")
            time.sleep(6 * 3600)
        except Exception:
            log.exception("worker cycle failed")
        time.sleep(60)
