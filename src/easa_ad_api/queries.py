"""Read side: turn database rows into the JSON the API serves."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from easa_ad_api.config import ARCHIVE_URL, INTERIM_URL, SOURCES
from easa_ad_api.numbers import ISSUER_NAMES, normalize_issuer
from easa_ad_api.store import key

SOURCE_PAGES = {"interim": INTERIM_URL, "archive": ARCHIVE_URL, "csv": INTERIM_URL}
STATUS_BASIS = {
    "active": "No supersedure, cancellation, withdrawal or revision signal seen in EASA's published data.",
    "proposed": "Proposed AD (PAD): published for consultation, not an AD.",
}


@dataclass
class Freshness:
    as_of: str | None
    stale: bool
    stale_warning: str | None
    sources: dict[str, dict[str, Any]]


def freshness(conn: sqlite3.Connection, stale_after_hours: float, now: datetime | None = None) -> Freshness:
    now = now or datetime.now(UTC)
    sources: dict[str, dict[str, Any]] = {}
    for source in SOURCES:
        ok = conn.execute(
            "SELECT started_at, rows, message, origin FROM sync_run WHERE source=? AND ok=1"
            " ORDER BY started_at DESC LIMIT 1",
            (source,),
        ).fetchone()
        last = conn.execute(
            "SELECT started_at, ok, message FROM sync_run WHERE source=? ORDER BY id DESC LIMIT 1",
            (source,),
        ).fetchone()
        sources[source] = {
            "last_successful_sync": ok["started_at"] if ok else None,
            "rows_in_last_sync": ok["rows"] if ok else None,
            "last_sync_note": ok["message"] if ok else None,
            "last_attempt": last["started_at"] if last else None,
            "last_attempt_ok": bool(last["ok"]) if last else None,
            "last_error": None if (last is None or last["ok"]) else last["message"],
        }
    interim = sources["interim"]["last_successful_sync"]
    threshold = timedelta(hours=stale_after_hours)
    if interim is None:
        return Freshness(
            None,
            True,
            "The EASA interim page has never been synced. The data may be incomplete.",
            sources,
        )
    age = now - datetime.fromisoformat(interim)
    stale = age > threshold
    warning = None
    if stale:
        hours = age.total_seconds() / 3600
        warning = (
            f"Stale data: the last successful sync of the EASA interim page was {hours:.1f} hours ago"
            f" ({interim}), more than the {stale_after_hours:g} hour limit. Newer ADs may be missing."
        )
    return Freshness(interim, stale, warning, sources)


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass
class Filters:
    type: str | None = None
    match: str = "exact"
    holder: str | None = None
    issuer: str | None = None
    pub_class: str | None = None
    status: str | None = None
    q: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    issued_from: str | None = None
    issued_to: str | None = None

    def where(self, alias: str = "p") -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if self.type:
            if self.match == "contains":
                clauses.append(
                    f"({alias}.types_raw LIKE ? ESCAPE '\\' OR {alias}.number IN"
                    " (SELECT number FROM publication_type WHERE type_name LIKE ? ESCAPE '\\'))"
                )
                args += [_like(self.type), _like(self.type)]
            else:
                clauses.append(
                    f"{alias}.number IN (SELECT number FROM publication_type WHERE type_key = ?"
                    " OR EXISTS (SELECT 1 FROM json_each(model_keys) WHERE value = ?))"
                )
                args += [key(self.type), key(self.type)]
        if self.holder:
            clauses.append(f"{alias}.holders LIKE ? ESCAPE '\\'")
            args.append(_like(self.holder))
        if self.issuer:
            clauses.append(f"{alias}.issuer = ?")
            args.append(normalize_issuer(self.issuer))
        if self.pub_class:
            clauses.append(f"{alias}.class = ?")
            args.append(self.pub_class.upper())
        if self.status:
            clauses.append(f"{alias}.status = ?")
            args.append(self.status)
        if self.q:
            clauses.append(f"{alias}.subject LIKE ? ESCAPE '\\'")
            args.append(_like(self.q))
        for column, op, value in (
            ("effective_date", ">=", self.effective_from),
            ("effective_date", "<=", self.effective_to),
            ("issue_date", ">=", self.issued_from),
            ("issue_date", "<=", self.issued_to),
        ):
            if value:
                clauses.append(f"{alias}.{column} {op} ?")
                args.append(value)
        return (" AND ".join(clauses) or "1=1"), args


def _types(conn: sqlite3.Connection, number: str) -> list[dict[str, Any]]:
    return [
        {
            "holder": t["holder"],
            "type": t["type_name"],
            "models": json.loads(t["models"]),
            "parse_confidence": t["confidence"],
        }
        for t in conn.execute("SELECT * FROM publication_type WHERE number=? ORDER BY rowid", (number,))
    ]


def _documents(conn: sqlite3.Connection, number: str) -> list[dict[str, Any]]:
    return [
        {"url": d["url"], "label": d["label"], "kind": d["kind"], "listed_on": d["source"]}
        for d in conn.execute("SELECT * FROM document WHERE number=? ORDER BY position", (number,))
    ]


def publication_json(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    number = row["number"]
    sources = json.loads(row["sources"])
    return {
        "number": number,
        "class": row["class"],
        "issuer": row["issuer"],
        "issuer_name": ISSUER_NAMES.get(row["issuer"]),
        "emergency": bool(row["emergency"]),
        "revision": row["revision"],
        "issue_date": row["issue_date"],
        "issue_date_uncertain": bool(row["issue_date_uncertain"]),
        "effective_date": row["effective_date"],
        "subject": row["subject"],
        "holders": json.loads(row["holders"]),
        "types": _types(conn, number),
        "types_text": row["types_raw"],
        "status": row["status"],
        "status_basis": STATUS_BASIS.get(row["status"], f"Signal: {row['status_source']}"),
        "superseded_by": row["superseded_by"],
        "superseded_by_text": row["superseded_by_raw"],
        "official_documents": _documents(conn, number),
        "official_source_page": SOURCE_PAGES[row["primary_source"]],
        "listed_on": sources,
        "first_seen": row["first_seen"],
        "updated_at": row["updated_at"],
    }


def search(
    conn: sqlite3.Connection, filters: Filters, limit: int, offset: int
) -> tuple[int, list[dict[str, Any]]]:
    where, args = filters.where()
    total = conn.execute(f"SELECT COUNT(*) FROM publication p WHERE {where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM publication p WHERE {where}"
        " ORDER BY p.issue_date DESC, p.number DESC LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    return total, [publication_json(conn, r) for r in rows]


def _summary(conn: sqlite3.Connection, number: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT number, status, issue_date, subject FROM publication WHERE number=?", (number,)
    ).fetchone()
    if row is None:
        return {"number": number, "known": False}
    return {
        "number": number,
        "known": True,
        "status": row["status"],
        "issue_date": row["issue_date"],
        "subject": row["subject"],
    }


def _older(conn: sqlite3.Connection, number: str) -> list[str]:
    found = [
        r["number"] for r in conn.execute("SELECT number FROM publication WHERE superseded_by=?", (number,))
    ]
    ex = conn.execute("SELECT supersedes, revises FROM extract WHERE number=?", (number,)).fetchone()
    if ex:
        found += json.loads(ex["supersedes"]) + json.loads(ex["revises"])
    return sorted(dict.fromkeys(found))


def supersedure(conn: sqlite3.Connection, number: str, max_steps: int = 25) -> dict[str, Any]:
    """Direct links both ways plus the chain of older and newer publications."""
    row = conn.execute("SELECT superseded_by FROM publication WHERE number=?", (number,)).fetchone()
    newer: list[str] = []
    current = row["superseded_by"] if row else None
    while current and current not in newer and current != number and len(newer) < max_steps:
        newer.append(current)
        nxt = conn.execute("SELECT superseded_by FROM publication WHERE number=?", (current,)).fetchone()
        current = nxt["superseded_by"] if nxt else None
    older: list[str] = []
    frontier = _older(conn, number)
    while frontier and len(older) < max_steps:
        item = frontier.pop(0)
        if item in older or item == number:
            continue
        older.append(item)
        frontier += _older(conn, item)
    return {
        "supersedes": [_summary(conn, n) for n in _older(conn, number)],
        "superseded_by": _summary(conn, row["superseded_by"]) if row and row["superseded_by"] else None,
        "chain_older": [_summary(conn, n) for n in older],
        "chain_newer": [_summary(conn, n) for n in newer],
        "latest_in_chain": newer[-1] if newer else number,
    }


def pdf_extract(conn: sqlite3.Connection, number: str) -> dict[str, Any] | None:
    ex = conn.execute("SELECT * FROM extract WHERE number=?", (number,)).fetchone()
    if ex is None:
        return None
    return {
        "from_document": ex["document_url"],
        "issue_date": ex["pdf_issue_date"],
        "effective_date": ex["pdf_effective_date"],
        "supersedure": ex["supersedure_text"],
        "revision": ex["revision_text"],
        "foreign_ad": ex["foreign_ad"],
        "applicability_header": ex["applicability_header"],
        "extracted_at": ex["extracted_at"],
        "note": "Short factual fields read automatically from the official PDF header."
        " Read the official PDF for the full applicability and required actions.",
    }


def get_publication(conn: sqlite3.Connection, number: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM publication WHERE number=?", (number,)).fetchone()
    if row is None:
        return None
    data = publication_json(conn, row)
    data["supersedure"] = supersedure(conn, number)
    data["revisions"] = [
        r["number"]
        for r in conn.execute(
            "SELECT number FROM publication WHERE base_number=? AND number != ? ORDER BY revision",
            (row["base_number"], number),
        )
    ]
    data["pdf_extract"] = pdf_extract(conn, number)
    return data


def list_types(
    conn: sqlite3.Connection, q: str | None, holder: str | None, limit: int, offset: int
) -> tuple[int, list[dict[str, Any]]]:
    clauses, args = [], []
    if q:
        clauses.append("t.type_name LIKE ? ESCAPE '\\'")
        args.append(_like(q))
    if holder:
        clauses.append("t.holder LIKE ? ESCAPE '\\'")
        args.append(_like(holder))
    where = " AND ".join(clauses) or "1=1"
    base = (
        "SELECT MIN(t.type_name) AS type_name, MIN(t.holder) AS holder,"
        " COUNT(DISTINCT t.number) AS publications, MAX(p.issue_date) AS latest_issue_date,"
        " MAX(t.confidence = 'high') AS from_structured_list"
        f" FROM publication_type t JOIN publication p ON p.number = t.number WHERE {where}"
        " GROUP BY t.type_key"
    )
    total = conn.execute(f"SELECT COUNT(*) FROM ({base})", args).fetchone()[0]
    rows = conn.execute(
        f"{base} ORDER BY publications DESC, type_name LIMIT ? OFFSET ?", [*args, limit, offset]
    ).fetchall()
    return total, [
        {
            "type": r["type_name"],
            "holder": r["holder"],
            "publications": r["publications"],
            "latest_issue_date": r["latest_issue_date"],
            "parse_confidence": "high" if r["from_structured_list"] else "low",
        }
        for r in rows
    ]


CHANGE_TYPES = (
    "new",
    "status_changed",
    "effective_date_changed",
    "subject_changed",
    "issue_date_changed",
    "document_added",
    "imported",
)


def changes(
    conn: sqlite3.Connection,
    filters: Filters,
    *,
    since: str | None,
    after_id: int | None,
    include_imported: bool,
    limit: int,
    change_type: str | None = None,
    newest_first: bool = False,
) -> list[dict[str, Any]]:
    where, args = filters.where()
    clauses = [where]
    if change_type:
        clauses.append("e.change_type = ?")
        args.append(change_type)
    if not include_imported:
        clauses.append("e.change_type != 'imported'")
    if since:
        clauses.append("e.observed_at >= ?")
        args.append(since)
    if after_id is not None:
        clauses.append("e.id > ?")
        args.append(after_id)
    order = "DESC" if newest_first else "ASC"
    rows = conn.execute(
        "SELECT e.id, e.number, e.change_type, e.old_value, e.new_value, e.source, e.observed_at"
        " FROM change_event e JOIN publication p ON p.number = e.number"
        f" WHERE {' AND '.join(clauses)} ORDER BY e.id {order} LIMIT ?",
        [*args, limit],
    ).fetchall()
    out = []
    for r in rows:
        pub = conn.execute("SELECT * FROM publication WHERE number=?", (r["number"],)).fetchone()
        out.append(
            {
                "id": r["id"],
                "change_type": r["change_type"],
                "old_value": r["old_value"],
                "new_value": r["new_value"],
                "detected_from": r["source"],
                "observed_at": r["observed_at"],
                "publication": publication_json(conn, pub),
            }
        )
    return out


def counts(conn: sqlite3.Connection) -> dict[str, Any]:
    def grouped(column: str) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in conn.execute(f"SELECT {column}, COUNT(*) FROM publication GROUP BY 1 ORDER BY 2 DESC")
        }

    return {
        "publications": conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0],
        "by_class": grouped("class"),
        "by_status": grouped("status"),
        "by_primary_source": grouped("primary_source"),
        "issue_date_uncertain": conn.execute(
            "SELECT COUNT(*) FROM publication WHERE issue_date_uncertain=1"
        ).fetchone()[0],
        "pdf_extracts": conn.execute("SELECT COUNT(*) FROM extract").fetchone()[0],
        "change_events": conn.execute(
            "SELECT COUNT(*) FROM change_event WHERE change_type != 'imported'"
        ).fetchone()[0],
    }


def reconciliation(conn: sqlite3.Connection) -> dict[str, Any]:
    open_gaps = [
        {"period": r["period"], "number": r["number"], "observed_at": r["observed_at"]}
        for r in conn.execute(
            "SELECT * FROM reconciliation_gap WHERE resolved_at IS NULL ORDER BY period, number"
        )
    ]
    loaded = [
        r["period"]
        for r in conn.execute("SELECT period FROM csv_period WHERE ingested_at IS NOT NULL ORDER BY period")
    ]
    return {"csv_periods_loaded": loaded, "missing_from_interim": open_gaps, "alert": bool(open_gaps)}
