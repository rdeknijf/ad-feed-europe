import io
import sqlite3
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from easa_ad_api import ingest, store

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE_TIME = "2026-11-04T19:37:20+00:00"
INTERIM_TIME = "2026-11-04T19:37:21+00:00"


def csv_zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("easa_biweekly_22-2026.csv", text)
    return buf.getvalue()


@pytest.fixture
def zip_csv() -> Callable[[str], bytes]:
    return csv_zip


@pytest.fixture
def archive_page() -> str:
    return (FIXTURES / "archive_trimmed.html").read_text()


@pytest.fixture
def interim_page() -> str:
    return (FIXTURES / "interim.html").read_text()


@pytest.fixture
def csv_text() -> str:
    return (FIXTURES / "biweekly_22_reconstructed.csv").read_text()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "test.db"
    monkeypatch.setenv("EASA_AD_DATABASE", str(db))
    monkeypatch.setenv("EASA_AD_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("EASA_AD_STALE_HOURS", "6")
    monkeypatch.setenv("EASA_AD_REQUEST_DELAY", "0")
    return db


@pytest.fixture
def conn(env: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(env)
    yield c
    c.close()


def load_extracts(conn: sqlite3.Connection, now: str) -> None:
    for path in sorted((FIXTURES / "pdf-text").glob("*.txt")):
        ingest.store_extract(
            conn, path.stem, f"https://example.invalid/{path.stem}.pdf", "0" * 64, path.read_text(), now
        )
    conn.commit()


@pytest.fixture
def loaded(
    conn: sqlite3.Connection, archive_page: str, interim_page: str, csv_text: str
) -> sqlite3.Connection:
    """Archive baseline, interim, CSV period 22 and the PDF header extracts."""
    ingest.ingest_archive(conn, archive_page, "fixture", ARCHIVE_TIME)
    ingest.ingest_interim(conn, interim_page, "fixture", INTERIM_TIME)
    ingest.ingest_csv(conn, csv_zip(csv_text), 22, "fixture", "2026-11-04T19:46:54+00:00")
    load_extracts(conn, "2026-11-04T19:47:16+00:00")
    store.rebuild(conn, "2026-11-04T19:47:16+00:00", source="pdfs", baseline=False)
    return conn


@pytest.fixture
def client(loaded: sqlite3.Connection) -> TestClient:
    from easa_ad_api.api import app

    return TestClient(app)
