import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from easa_ad_api import ingest
from easa_ad_api.config import ARCHIVE_URL, INTERIM_URL, USER_AGENT

FIXTURES = Path(__file__).parent / "fixtures"


def make_fetcher(handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float]) -> ingest.Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ingest.Fetcher(client=client, delay=2.5, sleep=sleeps.append)


def test_fetcher_sends_user_agent_and_spaces_requests() -> None:
    seen: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text="ok")

    fetcher = make_fetcher(handler, sleeps)
    fetcher.get("https://www.easa.europa.eu/a")
    fetcher.get("https://www.easa.europa.eu/b")
    assert seen == [USER_AGENT, USER_AGENT]
    assert "@" not in USER_AGENT
    assert USER_AGENT == "ADFeedEurope/1.0 (+https://adfeed.deknijf.com/terms)"
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 2.5
    assert fetcher.requests == 2


@pytest.mark.parametrize("status", [403, 429])
def test_fetcher_stops_on_blocking(status: int) -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(status), [])
    with pytest.raises(ingest.BlockedError):
        fetcher.get("https://www.easa.europa.eu/x")


def test_fetcher_raises_on_server_error() -> None:
    fetcher = make_fetcher(lambda r: httpx.Response(500), [])
    with pytest.raises(httpx.HTTPStatusError):
        fetcher.get("https://www.easa.europa.eu/x")


def test_live_entry_points_with_mock_transport(
    conn: sqlite3.Connection,
    tmp_path: Path,
    archive_page: str,
    interim_page: str,
    csv_text: str,
    zip_csv: Callable[[str], bytes],
) -> None:
    pages = {ARCHIVE_URL: archive_page.encode(), INTERIM_URL: interim_page.encode()}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in pages:
            return httpx.Response(200, content=pages[url])
        if url == "https://www.easa.europa.eu/en/downloads/144341/en":
            return httpx.Response(200, content=zip_csv(csv_text))
        return httpx.Response(404)

    fetcher = make_fetcher(handler, [])
    raw = tmp_path / "raw"
    assert ingest.fetch_archive(conn, fetcher, raw).rows == 44
    assert ingest.fetch_interim(conn, fetcher, raw).rows == 67
    period = ingest.next_csv_period(conn)
    assert period is not None
    assert period["period"] == 22
    assert ingest.fetch_csv(conn, fetcher, period["period"], period["csv_url"], raw).rows == 20
    following = ingest.next_csv_period(conn)
    assert following is not None
    assert following["period"] == 21
    assert sorted(p.name for p in raw.iterdir()) == ["archive.html", "biweekly-22.zip", "interim.html"]


def test_pdf_candidates_and_fetch(
    loaded: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded.execute("DELETE FROM extract")
    loaded.commit()
    candidates = ingest.pdf_candidates(loaded)
    numbers = [c.number for c in candidates]
    assert "2026-0213" in numbers
    assert all(not n.startswith(("US-", "CF-", "BR-", "26-")) for n in numbers)
    assert "2026-0203" in numbers
    assert len(ingest.pdf_candidates(loaded, include_archive=True)) > len(candidates)

    text = (FIXTURES / "pdf-text" / "2026-0199.txt").read_text()
    monkeypatch.setattr(ingest, "pdf_to_text", lambda path: text)

    def handler(request: httpx.Request) -> httpx.Response:
        if "144412" in str(request.url):
            return httpx.Response(
                200, content=b"<html>not a pdf</html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(200, content=b"%PDF-1.7 fake")

    picked = [c for c in candidates if c.number in ("2026-0213", "2026-0199")]
    result = ingest.fetch_pdfs(loaded, make_fetcher(handler, []), tmp_path, picked)
    assert result.new == 1
    assert "2026-0213" in result.message
    stored = list((tmp_path / "pdf-text").glob("*.txt"))
    assert len(stored) == 1
    assert not list(tmp_path.rglob("*.pdf"))
    ex = loaded.execute("SELECT * FROM extract WHERE number='2026-0199'").fetchone()
    assert ex["supersedes"] == '["2026-0086"]'
    err = loaded.execute(
        "SELECT fetch_error FROM document WHERE number='2026-0213' AND fetch_error IS NOT NULL"
    )
    assert "not a PDF" in err.fetchone()[0]

    loaded.execute("DELETE FROM extract")
    loaded.commit()
    assert ingest.reparse_pdfs(loaded, tmp_path) == 1
    assert loaded.execute("SELECT COUNT(*) FROM extract").fetchone()[0] == 1


def test_pdf_to_text_requires_poppler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="pdftotext"):
        ingest.pdf_to_text(tmp_path / "x.pdf")
