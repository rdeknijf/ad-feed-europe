"""SQLite storage: source records, the merged publication view and change events."""

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from easa_ad_api.numbers import classify, status_from_subject, superseded_by_reference
from easa_ad_api.parsers import CsvPeriod, Record

# Field priority when sources disagree: the interim page is the newest.
SOURCE_PRIORITY = ("interim", "archive", "csv")
EXPLICIT_STATUSES = ("superseded", "cancelled", "withdrawn", "not_adopted")


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(files("easa_ad_api").joinpath("schema.sql").read_text())
    return conn


def key(text: str) -> str:
    """Normalize a type or model designation for exact matching."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def content_hash(data: dict[str, Any]) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


# Sync bookkeeping ----------------------------------------------------------


def start_sync(conn: sqlite3.Connection, source: str, origin: str, started_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO sync_run (source, started_at, origin) VALUES (?, ?, ?)",
        (source, started_at, origin),
    )
    conn.commit()
    return cur.lastrowid or 0


def finish_sync(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    ok: bool,
    finished_at: str,
    rows: int = 0,
    new_rows: int = 0,
    changed_rows: int = 0,
    message: str = "",
) -> None:
    conn.execute(
        "UPDATE sync_run SET ok=?, finished_at=?, rows=?, new_rows=?, changed_rows=?, message=? WHERE id=?",
        (int(ok), finished_at, rows, new_rows, changed_rows, message, run_id),
    )
    conn.commit()


def has_successful_sync(conn: sqlite3.Connection, source: str) -> bool:
    row = conn.execute("SELECT 1 FROM sync_run WHERE source=? AND ok=1 LIMIT 1", (source,))
    return row.fetchone() is not None


# Source records -----------------------------------------------------------


@dataclass
class UpsertResult:
    rows: int = 0
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)


def upsert_source_records(
    conn: sqlite3.Connection,
    source: str,
    records: Iterable[Record],
    now: str,
    *,
    full_snapshot: bool,
) -> UpsertResult:
    """Store one source's rows. Diff by number and content hash within that source."""
    existing = {
        row["number"]: row["content_hash"]
        for row in conn.execute("SELECT number, content_hash FROM source_record WHERE source=?", (source,))
    }
    result = UpsertResult()
    seen: set[str] = set()
    for record in records:
        if record.number in seen:
            continue
        seen.add(record.number)
        result.rows += 1
        data = record.as_json()
        digest = content_hash(data)
        old = existing.get(record.number)
        if old is None:
            result.new.append(record.number)
            conn.execute(
                "INSERT INTO source_record (number, source, content_hash, data, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (record.number, source, digest, json.dumps(data, ensure_ascii=False), now, now),
            )
        else:
            if old != digest:
                result.changed.append(record.number)
                _document_events(conn, record, source, now)
            conn.execute(
                "UPDATE source_record SET content_hash=?, data=?, last_seen=?, present=1"
                " WHERE number=? AND source=?",
                (digest, json.dumps(data, ensure_ascii=False), now, record.number, source),
            )
    if full_snapshot:
        gone = set(existing) - seen
        conn.executemany(
            "UPDATE source_record SET present=0 WHERE number=? AND source=?",
            [(number, source) for number in gone],
        )
    conn.commit()
    return result


def _document_events(conn: sqlite3.Connection, record: Record, source: str, now: str) -> None:
    """Log documents that a source added to a row it already listed."""
    row = conn.execute(
        "SELECT data FROM source_record WHERE number=? AND source=?", (record.number, source)
    ).fetchone()
    old_urls = {d["url"] for d in json.loads(row["data"])["documents"]}
    for doc in record.documents:
        if doc.url not in old_urls:
            conn.execute(
                "INSERT INTO change_event (number, change_type, old_value, new_value, source,"
                " observed_at) VALUES (?, 'document_added', NULL, ?, ?, ?)",
                (record.number, doc.url, source, now),
            )


def store_csv_periods(conn: sqlite3.Connection, periods: list[CsvPeriod], now: str) -> None:
    for p in periods:
        conn.execute(
            "INSERT INTO csv_period (period, date_from, date_to, csv_url, first_seen)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(period) DO UPDATE SET"
            " date_from=excluded.date_from, date_to=excluded.date_to, csv_url=excluded.csv_url",
            (p.period, p.date_from, p.date_to, p.csv_url, now),
        )
    conn.commit()


# Merge ---------------------------------------------------------------------


@dataclass
class Merged:
    row: dict[str, Any]
    types: list[dict[str, Any]]
    documents: list[dict[str, Any]]


def _merge_number(number: str, by_source: dict[str, dict[str, Any]], first_seen: str) -> Merged:
    ordered = [s for s in SOURCE_PRIORITY if s in by_source]
    primary = by_source[ordered[0]]
    archive_class = by_source["archive"]["pub_class"] if "archive" in by_source else None
    info = classify(number, archive_class)

    status, status_source, raw_ref, resolved = "active", None, None, None
    for source in ordered:
        signal = status_from_subject(by_source[source]["subject"])
        if signal:
            status, status_source = signal, f"subject:{source}"
            raw_ref, resolved = superseded_by_reference(by_source[source]["subject"])
            break
    if status == "active" and info.pub_class == "PAD":
        status = "proposed"

    def first(name: str) -> Any:
        for source in ordered:
            value = by_source[source].get(name)
            if value:
                return value
        return None

    row = {
        "number": number,
        "base_number": info.base_number,
        "revision": info.revision,
        "class": info.pub_class,
        "issuer": primary["issuer"] if ordered[0] == "interim" else info.issuer,
        "emergency": int(info.emergency or info.pub_class == "EAD"),
        "issue_date": primary["issue_date"] or first("issue_date"),
        "issue_date_uncertain": int(bool(primary["issue_date_uncertain"])),
        "effective_date": primary["effective_date"] or first("effective_date"),
        "subject": primary["subject"],
        "holders": json.dumps(primary["holders"] or first("holders") or [], ensure_ascii=False),
        "types_raw": primary["types_raw"] or first("types_raw") or "",
        "status": status,
        "status_source": status_source,
        "superseded_by_raw": raw_ref,
        "superseded_by": resolved,
        "primary_source": ordered[0],
        "sources": json.dumps(ordered),
        "first_seen": first_seen,
    }
    type_source = next((s for s in ordered if by_source[s]["types"]), ordered[0])
    types = [
        {
            "number": number,
            "holder": t["holder"],
            "type_name": t["type_name"],
            "type_key": key(t["type_name"]),
            "models": json.dumps(t["models"], ensure_ascii=False),
            "model_keys": json.dumps([key(m) for m in t["models"]], ensure_ascii=False),
            "confidence": t["confidence"],
        }
        for t in by_source[type_source]["types"]
    ]
    documents = []
    seen_urls: set[str] = set()
    for source in ordered:
        for doc in by_source[source]["documents"]:
            if doc["url"] in seen_urls:
                continue
            seen_urls.add(doc["url"])
            documents.append(
                {
                    "url": doc["url"],
                    "number": number,
                    "source": source,
                    "position": len(documents),
                    "label": doc["label"],
                    "kind": doc["kind"],
                }
            )
    return Merged(row, types, documents)


def _apply_cross_signals(merged: dict[str, Merged], conn: sqlite3.Connection) -> None:
    """Status signals that come from another publication: PDF supersedure and revisions."""
    for ex in conn.execute("SELECT number, supersedes, revises FROM extract"):
        for column, status in (("supersedes", "superseded"), ("revises", "revised")):
            for old in json.loads(ex[column]):
                target = merged.get(old)
                if target and target.row["status"] in ("active", "proposed"):
                    target.row.update(
                        status=status,
                        status_source=f"pdf:{ex['number']}",
                        superseded_by=ex["number"],
                        superseded_by_raw=None,
                    )
    latest: dict[str, tuple[int, str]] = {}
    for number, m in merged.items():
        base, rev = m.row["base_number"], m.row["revision"]
        if base not in latest or rev > latest[base][0]:
            latest[base] = (rev, number)
    for number, m in merged.items():
        rev, newest = latest[m.row["base_number"]]
        if newest != number and m.row["revision"] < rev and m.row["status"] == "active":
            m.row.update(
                status="revised", status_source="revision", superseded_by=newest, superseded_by_raw=None
            )


@dataclass
class RebuildResult:
    events: int = 0
    publications: int = 0


_FIELD_EVENTS = ("effective_date", "subject", "issue_date")


def rebuild(conn: sqlite3.Connection, now: str, *, source: str, baseline: bool) -> RebuildResult:
    """Rebuild the merged view from all source records and log what changed.

    A baseline load (first archive import) logs 'imported' events, which feeds skip.
    Field changes are logged only when the primary source stays the same, so a switch
    from archive to interim wording does not look like an edit.
    """
    by_number: dict[str, dict[str, dict[str, Any]]] = {}
    first_seen: dict[str, str] = {}
    for row in conn.execute("SELECT number, source, data, first_seen FROM source_record"):
        by_number.setdefault(row["number"], {})[row["source"]] = json.loads(row["data"])
        prev = first_seen.get(row["number"])
        if prev is None or row["first_seen"] < prev:
            first_seen[row["number"]] = row["first_seen"]

    merged = {n: _merge_number(n, s, first_seen[n]) for n, s in by_number.items()}
    _apply_cross_signals(merged, conn)

    old_rows = {r["number"]: dict(r) for r in conn.execute("SELECT * FROM publication")}
    fetch_meta: dict[tuple[str, str], dict[str, Any]] = {}
    for d in conn.execute("SELECT * FROM document"):
        fetch_meta[(d["url"], d["number"])] = dict(d)

    events: list[tuple[str, str, str | None, str | None, str, str]] = []
    for number, m in merged.items():
        old = old_rows.get(number)
        row = m.row
        if old is None:
            events.append((number, "imported" if baseline else "new", None, None, source, now))
            row["updated_at"] = now
            continue
        changed = False
        if old["status"] != row["status"]:
            events.append((number, "status_changed", old["status"], row["status"], source, now))
            changed = True
        if old["primary_source"] == row["primary_source"]:
            for name in _FIELD_EVENTS:
                if name == "subject" and old["status"] != row["status"]:
                    continue
                if (old[name] or None) != (row[name] or None):
                    events.append((number, f"{name}_changed", old[name], row[name], source, now))
                    changed = True
        row["updated_at"] = now if changed else old["updated_at"]

    with conn:
        conn.execute("DELETE FROM publication")
        conn.execute("DELETE FROM publication_type")
        conn.execute("DELETE FROM document")
        columns = list(next(iter(merged.values())).row) if merged else []
        if columns:
            conn.executemany(
                f"INSERT INTO publication ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                [tuple(m.row[c] for c in columns) for m in merged.values()],
            )
        conn.executemany(
            "INSERT INTO publication_type (number, holder, type_name, type_key, models, model_keys,"
            " confidence) VALUES (:number, :holder, :type_name, :type_key, :models, :model_keys,"
            " :confidence)",
            [t for m in merged.values() for t in m.types],
        )
        doc_rows = []
        for m in merged.values():
            for d in m.documents:
                meta = fetch_meta.get((d["url"], d["number"]), {})
                doc_rows.append(
                    {
                        **d,
                        "final_url": meta.get("final_url"),
                        "sha256": meta.get("sha256"),
                        "size": meta.get("size"),
                        "fetched_at": meta.get("fetched_at"),
                        "text_extracted": meta.get("text_extracted", 0),
                        "fetch_error": meta.get("fetch_error"),
                    }
                )
        conn.executemany(
            "INSERT INTO document (url, number, source, position, label, kind, final_url, sha256,"
            " size, fetched_at, text_extracted, fetch_error) VALUES (:url, :number, :source,"
            " :position, :label, :kind, :final_url, :sha256, :size, :fetched_at, :text_extracted,"
            " :fetch_error)",
            doc_rows,
        )
        conn.executemany(
            "INSERT INTO change_event (number, change_type, old_value, new_value, source, observed_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            events,
        )
    return RebuildResult(events=len(events), publications=len(merged))
