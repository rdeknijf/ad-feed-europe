"""Ingestion: fetch the EASA sources politely, store them and rebuild the merged view."""

import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from easa_ad_api import parsers, store
from easa_ad_api.config import ARCHIVE_URL, INTERIM_URL, USER_AGENT
from easa_ad_api.pdf import parse_header

log = logging.getLogger(__name__)

PDF_CLASSES = ("AD", "EAD", "ADR")


class BlockedError(RuntimeError):
    """EASA answered 403 or 429: stop all live calls."""


class Fetcher:
    """HTTP GET with a fixed User-Agent and a minimum gap between requests."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        delay: float = 2.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client or httpx.Client(timeout=120)
        self.client.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self._sleep = sleep
        self._last = 0.0
        self.requests = 0

    def get(self, url: str) -> httpx.Response:
        wait = self._last + self.delay - time.monotonic()
        if self._last and wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        self.requests += 1
        response = self.client.get(url, follow_redirects=True)
        if response.status_code in (403, 429):
            raise BlockedError(f"{url} answered HTTP {response.status_code}")
        response.raise_for_status()
        return response


@dataclass
class IngestResult:
    source: str
    rows: int
    new: int
    changed: int
    events: int
    message: str = ""


def _run(
    conn: sqlite3.Connection,
    source: str,
    origin: str,
    work: Callable[[str], tuple[int, int, int, int, str]],
    now: str | None = None,
) -> IngestResult:
    now = now or store.utcnow()
    run_id = store.start_sync(conn, source, origin, now)
    try:
        rows, new, changed, events, message = work(now)
    except Exception as exc:
        store.finish_sync(conn, run_id, ok=False, finished_at=store.utcnow(), message=repr(exc))
        raise
    store.finish_sync(
        conn,
        run_id,
        ok=True,
        finished_at=store.utcnow(),
        rows=rows,
        new_rows=new,
        changed_rows=changed,
        message=message,
    )
    return IngestResult(source, rows, new, changed, events, message)


def ingest_archive(conn: sqlite3.Connection, page: str, origin: str, now: str | None = None) -> IngestResult:
    def work(now: str) -> tuple[int, int, int, int, str]:
        records = parsers.parse_archive(page)
        baseline = not store.has_successful_sync(conn, "archive")
        up = store.upsert_source_records(conn, "archive", records, now, full_snapshot=True)
        rb = store.rebuild(conn, now, source="archive", baseline=baseline)
        note = "baseline import" if baseline else ""
        return up.rows, len(up.new), len(up.changed), rb.events, note

    return _run(conn, "archive", origin, work, now)


def ingest_interim(conn: sqlite3.Connection, page: str, origin: str, now: str | None = None) -> IngestResult:
    def work(now: str) -> tuple[int, int, int, int, str]:
        records, periods = parsers.parse_interim(page)
        if not records:
            raise parsers.SourceFormatError("interim page: table is empty")
        up = store.upsert_source_records(conn, "interim", records, now, full_snapshot=True)
        store.store_csv_periods(conn, periods, now)
        conn.executemany(
            "UPDATE reconciliation_gap SET resolved_at=? WHERE number=? AND resolved_at IS NULL",
            [(now, r.number) for r in records],
        )
        conn.commit()
        rb = store.rebuild(conn, now, source="interim", baseline=False)
        return up.rows, len(up.new), len(up.changed), rb.events, f"{len(periods)} csv periods listed"

    return _run(conn, "interim", origin, work, now)


def ingest_csv(
    conn: sqlite3.Connection, blob: bytes, period: int, origin: str, now: str | None = None
) -> IngestResult:
    """Store a biweekly CSV and record every number that the interim table lacks."""

    def work(now: str) -> tuple[int, int, int, int, str]:
        records = parsers.parse_csv_zip(blob)
        interim = {
            r["number"]: json.loads(r["data"]).get("issue_date")
            for r in conn.execute("SELECT number, data FROM source_record WHERE source='interim'")
        }
        interim_start = min((d for d in interim.values() if d), default=None)
        gaps = [
            r.number
            for r in records
            if r.number not in interim and interim_start is not None and (r.issue_date or "") >= interim_start
        ]
        up = store.upsert_source_records(conn, "csv", records, now, full_snapshot=False)
        conn.executemany(
            "INSERT OR IGNORE INTO reconciliation_gap (period, number, observed_at) VALUES (?, ?, ?)",
            [(period, n, now) for n in gaps],
        )
        conn.execute(
            "INSERT INTO csv_period (period, date_from, date_to, csv_url, first_seen, ingested_at)"
            " VALUES (?, '', '', ?, ?, ?) ON CONFLICT(period) DO UPDATE SET ingested_at=excluded.ingested_at",
            (period, origin, now, now),
        )
        conn.commit()
        rb = store.rebuild(conn, now, source="csv", baseline=False)
        if gaps:
            log.warning("csv period %s: %d numbers missing from interim table: %s", period, len(gaps), gaps)
        message = f"period {period}; missing from interim: {', '.join(gaps) or 'none'}"
        return up.rows, len(up.new), len(up.changed), rb.events, message

    return _run(conn, "csv", origin, work, now)


# PDFs ----------------------------------------------------------------------


@dataclass
class PdfCandidate:
    number: str
    url: str


def pdf_candidates(conn: sqlite3.Connection, *, include_archive: bool = False) -> list[PdfCandidate]:
    """EASA-issued ADs whose first official PDF has no extract yet (or a different URL).

    By default only rows whose primary source is the interim page: new items.
    """
    rows = conn.execute(
        f"""
        SELECT p.number, p.primary_source, d.url, e.document_url
        FROM publication p
        JOIN document d ON d.number = p.number AND d.kind = 'pdf'
        LEFT JOIN extract e ON e.number = p.number
        WHERE p.issuer = 'EASA' AND p.class IN ({",".join("?" * len(PDF_CLASSES))})
        ORDER BY p.issue_date DESC, p.number DESC, d.position ASC
        """,
        PDF_CLASSES,
    )
    picked: dict[str, PdfCandidate] = {}
    done: set[str] = set()
    for row in rows:
        if row["number"] in picked or row["number"] in done:
            continue
        done.add(row["number"])
        if not include_archive and row["primary_source"] != "interim":
            continue
        if row["document_url"] == row["url"]:
            continue
        picked[row["number"]] = PdfCandidate(row["number"], row["url"])
    return list(picked.values())


def pdf_to_text(pdf_path: Path) -> str:
    if shutil.which("pdftotext") is None:
        raise RuntimeError("pdftotext (poppler-utils) is not installed")
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], check=True, capture_output=True, timeout=60
    )
    return out.stdout.decode("utf-8", errors="replace")


def store_extract(conn: sqlite3.Connection, number: str, url: str, sha256: str, text: str, now: str) -> None:
    fields = parse_header(text)
    conn.execute(
        "INSERT INTO extract (number, document_url, sha256, pdf_issue_date, pdf_effective_date,"
        " supersedure_text, supersedes, revision_text, revises, foreign_ad, applicability_header,"
        " extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(number) DO UPDATE SET"
        " document_url=excluded.document_url, sha256=excluded.sha256,"
        " pdf_issue_date=excluded.pdf_issue_date, pdf_effective_date=excluded.pdf_effective_date,"
        " supersedure_text=excluded.supersedure_text, supersedes=excluded.supersedes,"
        " revision_text=excluded.revision_text, revises=excluded.revises,"
        " foreign_ad=excluded.foreign_ad, applicability_header=excluded.applicability_header,"
        " extracted_at=excluded.extracted_at",
        (
            number,
            url,
            sha256,
            fields.issue_date,
            fields.effective_date,
            fields.supersedure,
            json.dumps(fields.supersedes),
            fields.revision,
            json.dumps(fields.revises),
            fields.foreign_ad,
            fields.applicability,
            now,
        ),
    )


def text_path(private_dir: Path, number: str, sha256: str) -> Path:
    return private_dir / "pdf-text" / f"{number}__{sha256[:16]}.txt"


def fetch_pdfs(
    conn: sqlite3.Connection,
    fetcher: Fetcher,
    private_dir: Path,
    candidates: list[PdfCandidate],
    now: str | None = None,
) -> IngestResult:
    """Fetch each PDF once, keep only its text in the private store, parse the header.

    The PDF bytes live in a temporary directory and are deleted after extraction.
    """

    def work(now: str) -> tuple[int, int, int, int, str]:
        done = 0
        errors = []
        for c in candidates:
            response = fetcher.get(c.url)
            if not response.content.startswith(b"%PDF"):
                errors.append(c.number)
                conn.execute(
                    "UPDATE document SET fetch_error=?, fetched_at=? WHERE url=? AND number=?",
                    (f"not a PDF: {response.headers.get('content-type')}", now, c.url, c.number),
                )
                continue
            digest = hashlib.sha256(response.content).hexdigest()
            with tempfile.TemporaryDirectory() as tmp:
                pdf = Path(tmp) / "doc.pdf"
                pdf.write_bytes(response.content)
                text = pdf_to_text(pdf)
            target = text_path(private_dir, c.number, digest)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            conn.execute(
                "UPDATE document SET final_url=?, sha256=?, size=?, fetched_at=?, text_extracted=1,"
                " fetch_error=NULL WHERE url=? AND number=?",
                (str(response.url), digest, len(response.content), now, c.url, c.number),
            )
            store_extract(conn, c.number, c.url, digest, text, now)
            conn.commit()
            done += 1
        rb = store.rebuild(conn, now, source="pdfs", baseline=False)
        return len(candidates), done, 0, rb.events, f"errors: {', '.join(errors) or 'none'}"

    return _run(conn, "pdfs", "pdf documents", work, now)


def reparse_pdfs(conn: sqlite3.Connection, private_dir: Path, now: str | None = None) -> int:
    """Re-run the header parser over the stored texts (no network)."""
    now = now or store.utcnow()
    count = 0
    for doc in conn.execute(
        "SELECT number, url, sha256 FROM document WHERE text_extracted=1 AND sha256 IS NOT NULL"
    ).fetchall():
        path = text_path(private_dir, doc["number"], doc["sha256"])
        if path.exists():
            store_extract(conn, doc["number"], doc["url"], doc["sha256"], path.read_text(), now)
            count += 1
    conn.commit()
    store.rebuild(conn, now, source="pdfs", baseline=False)
    return count


# Live entry points ---------------------------------------------------------


def save_raw(raw_dir: Path | None, name: str, body: bytes) -> None:
    """Keep the last raw response per source for debugging and re-parsing (private)."""
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / name).write_bytes(body)


def fetch_archive(conn: sqlite3.Connection, fetcher: Fetcher, raw_dir: Path | None = None) -> IngestResult:
    response = fetcher.get(ARCHIVE_URL)
    save_raw(raw_dir, "archive.html", response.content)
    return ingest_archive(conn, response.text, ARCHIVE_URL)


def fetch_interim(conn: sqlite3.Connection, fetcher: Fetcher, raw_dir: Path | None = None) -> IngestResult:
    response = fetcher.get(INTERIM_URL)
    save_raw(raw_dir, "interim.html", response.content)
    return ingest_interim(conn, response.text, INTERIM_URL)


def next_csv_period(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM csv_period WHERE ingested_at IS NULL AND csv_url != '' ORDER BY period DESC LIMIT 1"
    ).fetchone()


def fetch_csv(
    conn: sqlite3.Connection, fetcher: Fetcher, period: int, url: str, raw_dir: Path | None = None
) -> IngestResult:
    blob = fetcher.get(url).content
    save_raw(raw_dir, f"biweekly-{period}.zip", blob)
    return ingest_csv(conn, blob, period, url)
