import json
import sqlite3
from collections import Counter
from collections.abc import Callable

import pytest

from easa_ad_api import ingest, store
from easa_ad_api.parsers import SourceFormatError, parse_archive, parse_interim

NOW = "2026-11-05T10:00:00+00:00"


def events(conn: sqlite3.Connection) -> Counter[str]:
    return Counter(r[0] for r in conn.execute("SELECT change_type FROM change_event"))


def pub(conn: sqlite3.Connection, number: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM publication WHERE number=?", (number,)).fetchone()


def test_archive_baseline_is_imported_not_new(conn: sqlite3.Connection, archive_page: str) -> None:
    result = ingest.ingest_archive(conn, archive_page, "fixture", NOW)
    assert result.message == "baseline import"
    assert events(conn) == Counter({"imported": 44})
    again = ingest.ingest_archive(conn, archive_page, "fixture", NOW)
    assert (again.new, again.changed, again.events) == (0, 0, 0)


def test_interim_after_archive_emits_new_only_for_unseen(
    conn: sqlite3.Connection, archive_page: str, interim_page: str
) -> None:
    ingest.ingest_archive(conn, archive_page, "fixture", NOW)
    ingest.ingest_interim(conn, interim_page, "fixture", NOW)
    counts = events(conn)
    overlap = {r.number for r in parse_archive(archive_page)} & {
        r.number for r in parse_interim(interim_page)[0]
    }
    assert len(overlap) >= 7
    assert counts["new"] == 67 - len(overlap)
    assert "subject_changed" not in counts
    row = pub(conn, "2026-0203")
    assert row["primary_source"] == "interim"
    assert row["class"] == "AD"


def test_same_source_changes_are_logged(conn: sqlite3.Connection, interim_page: str) -> None:
    ingest.ingest_interim(conn, interim_page, "fixture", NOW)
    changed = (
        interim_page.replace("<td>2026-11-18</td>", "<td>2026-11-25</td>", 1)
        .replace("Landing Gear – Main Landing Gear Barrel – Inspection", "SUPERSEDED BY EASA AD 2026-0299", 1)
        .replace(
            'href="/en/downloads/144417/en">PDF</a>',
            'href="/en/downloads/144417/en">PDF</a> <a href="/en/downloads/199999/en">PDF 2</a>',
            1,
        )
    )
    ingest.ingest_interim(conn, changed, "fixture", "2026-11-05T11:00:00+00:00")
    rows = conn.execute(
        "SELECT change_type, old_value, new_value FROM change_event WHERE number='2026-0212' ORDER BY id"
    ).fetchall()
    kinds = [r["change_type"] for r in rows]
    assert kinds == ["new", "document_added", "status_changed", "effective_date_changed"]
    assert rows[2]["new_value"] == "superseded"
    row = pub(conn, "2026-0212")
    assert row["superseded_by"] == "2026-0299"
    assert row["updated_at"] == "2026-11-05T11:00:00+00:00"


def test_row_leaving_interim_is_kept(conn: sqlite3.Connection, interim_page: str) -> None:
    ingest.ingest_interim(conn, interim_page, "fixture", NOW)
    trimmed = interim_page.replace("<td>2026-0212</td>", "<td>2026-9999</td>", 1)
    ingest.ingest_interim(conn, trimmed, "fixture", NOW)
    present = conn.execute(
        "SELECT present FROM source_record WHERE number='2026-0212' AND source='interim'"
    ).fetchone()[0]
    assert present == 0
    assert pub(conn, "2026-0212") is not None


def test_pdf_signals_supersede_and_revise(loaded: sqlite3.Connection) -> None:
    assert pub(loaded, "2025-0231")["status"] == "superseded"
    assert pub(loaded, "2025-0231")["status_source"] == "pdf:2026-0211"
    assert pub(loaded, "2024-0205")["superseded_by"] == "2026-0206"
    assert pub(loaded, "2026-0086")["status_source"] == "subject:archive"
    assert pub(loaded, "26-133")["status"] == "proposed"


def test_revision_number_marks_base_revised(conn: sqlite3.Connection) -> None:
    def record(number: str) -> dict:
        return {
            "number": number,
            "source": "archive",
            "pub_class": "AD",
            "issuer": "ES",
            "issue_date": "2006-01-01",
            "issue_date_uncertain": False,
            "effective_date": None,
            "subject": "Wing – Inspection",
            "holders": ["X"],
            "types_raw": "",
            "types": [],
            "documents": [],
        }

    for number in ("ES-06-00", "ES-06-00R1"):
        data = record(number)
        conn.execute(
            "INSERT INTO source_record (number, source, content_hash, data, first_seen, last_seen)"
            " VALUES (?, 'archive', 'h', ?, ?, ?)",
            (number, json.dumps(data), NOW, NOW),
        )
    store.rebuild(conn, NOW, source="archive", baseline=True)
    assert pub(conn, "ES-06-00")["status"] == "revised"
    assert pub(conn, "ES-06-00")["superseded_by"] == "ES-06-00R1"
    assert pub(conn, "ES-06-00R1")["status"] == "active"


def test_csv_reconciliation_gap_and_resolution(
    conn: sqlite3.Connection, interim_page: str, csv_text: str, zip_csv: Callable[[str], bytes]
) -> None:
    without = interim_page.replace("<td>US-2026-20-14</td>", "<td>US-2026-99-99</td>", 1)
    ingest.ingest_interim(conn, without, "fixture", NOW)
    result = ingest.ingest_csv(conn, zip_csv(csv_text), 22, "fixture", NOW)
    assert "US-2026-20-14" in result.message
    gaps = conn.execute("SELECT number FROM reconciliation_gap WHERE resolved_at IS NULL").fetchall()
    assert [g[0] for g in gaps] == ["US-2026-20-14"]
    assert pub(conn, "US-2026-20-14")["primary_source"] == "csv"
    ingest.ingest_interim(conn, interim_page, "fixture", "2026-11-05T12:00:00+00:00")
    assert (
        conn.execute("SELECT COUNT(*) FROM reconciliation_gap WHERE resolved_at IS NULL").fetchone()[0] == 0
    )
    assert pub(conn, "US-2026-20-14")["primary_source"] == "interim"


def test_failed_parse_records_failed_sync(conn: sqlite3.Connection) -> None:
    with pytest.raises(SourceFormatError, match="ad-list"):
        ingest.ingest_interim(conn, "<html></html>", "fixture", NOW)
    row = conn.execute("SELECT ok, message FROM sync_run").fetchone()
    assert row["ok"] == 0
    assert "SourceFormatError" in row["message"]


def test_duplicate_numbers_in_one_source_count_once(conn: sqlite3.Connection, interim_page: str) -> None:
    records, _ = parse_interim(interim_page)
    result = store.upsert_source_records(conn, "interim", [records[0], records[0]], NOW, full_snapshot=False)
    assert result.rows == 1
