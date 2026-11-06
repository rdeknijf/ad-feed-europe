from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from easa_ad_api import cli, ingest, store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_ingest_from_files(
    runner: CliRunner, env: Path, tmp_path: Path, csv_text: str, zip_csv: Callable[[str], bytes]
) -> None:
    result = runner.invoke(
        cli.main, ["ingest-archive", "--from-file", str(FIXTURES / "archive_trimmed.html")]
    )
    assert result.exit_code == 0, result.output
    assert "archive: 44 rows" in result.output
    result = runner.invoke(cli.main, ["ingest-interim", "--from-file", str(FIXTURES / "interim.html")])
    assert "interim: 67 rows" in result.output
    zipped = tmp_path / "p22.zip"
    zipped.write_bytes(zip_csv(csv_text))
    result = runner.invoke(cli.main, ["ingest-csv", "--from-file", str(zipped)])
    assert result.exit_code != 0
    result = runner.invoke(cli.main, ["ingest-csv", "--period", "22", "--from-file", str(zipped)])
    assert "csv: 20 rows" in result.output
    result = runner.invoke(cli.main, ["fetch-pdfs", "--dry-run", "--limit", "3"])
    assert "3 PDFs would be fetched" in result.output
    result = runner.invoke(cli.main, ["reparse-pdfs"])
    assert "0 texts parsed" in result.output


def test_live_commands_use_fetcher(runner: CliRunner, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    ok = ingest.IngestResult("x", 1, 1, 0, 0)
    monkeypatch.setattr(ingest, "fetch_archive", lambda conn, f, raw: calls.append("archive") or ok)
    monkeypatch.setattr(ingest, "fetch_interim", lambda conn, f, raw: calls.append("interim") or ok)
    monkeypatch.setattr(ingest, "fetch_csv", lambda conn, f, p, u, raw: calls.append(f"csv{p}") or ok)
    monkeypatch.setattr(ingest, "fetch_pdfs", lambda conn, f, d, c: calls.append("pdfs") or ok)
    assert runner.invoke(cli.main, ["ingest-archive"]).exit_code == 0
    assert runner.invoke(cli.main, ["ingest-interim"]).exit_code == 0
    result = runner.invoke(cli.main, ["ingest-csv"])
    assert "no CSV period" in result.output
    conn = store.connect(env)
    conn.execute(
        "INSERT INTO csv_period VALUES (22, '2026-10-12', '2026-10-25', 'https://x.invalid/c', 'n', NULL)"
    )
    conn.commit()
    conn.close()
    assert runner.invoke(cli.main, ["ingest-csv"]).exit_code == 0
    assert runner.invoke(cli.main, ["ingest-csv", "--period", "22"]).exit_code == 0
    assert runner.invoke(cli.main, ["fetch-pdfs"]).exit_code == 0
    assert calls == ["archive", "interim", "csv22", "csv22", "pdfs"]


def test_due(env: Path) -> None:
    conn = store.connect(env)
    assert cli._due(conn, "interim", timedelta(minutes=30))
    recent = datetime.now(UTC).isoformat()
    conn.execute("INSERT INTO sync_run (source, started_at) VALUES ('interim', ?)", (recent,))
    assert not cli._due(conn, "interim", timedelta(minutes=30))
    assert cli._due(conn, "interim", timedelta(0))
    conn.close()


def test_serve_passes_port(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    assert runner.invoke(cli.main, ["serve", "--port", "8700"]).exit_code == 0
    assert seen["port"] == 8700
    assert seen["app"] == "easa_ad_api.api:app"
