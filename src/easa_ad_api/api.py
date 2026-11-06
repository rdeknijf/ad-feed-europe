"""FastAPI app: JSON API, Atom feeds, landing page and terms."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.resources import files
from typing import Annotated, Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape

from easa_ad_api import queries, store
from easa_ad_api.config import (
    APP_TITLE,
    ARCHIVE_URL,
    DISCLAIMER,
    EASA_COPYRIGHT_URL,
    EASA_FAQ_URL,
    EASA_TOOL_URL,
    INTERIM_URL,
    REPO_URL,
    SOURCE_NAME,
    UNOFFICIAL_NOTICE,
    get_settings,
)

DESCRIPTION = f"""
**{UNOFFICIAL_NOTICE}** **{DISCLAIMER}**

Free JSON API and Atom feeds over the airworthiness directive (AD) list that EASA publishes
on its interim page, its AD archive page and its biweekly CSV lists. EASA set these up while
its Safety Publications Tool was unavailable (November 2026). No keys, no accounts.

Every record links to the EASA page that lists it, and to the official EASA document where EASA
lists one. EASA's PDF files are never kept or served here. Every JSON response carries the time
of the last successful interim sync and a stale flag; `/v1/status` gives the time per source.
See [/terms](/terms).
"""

app = FastAPI(
    title=f"{APP_TITLE} (unofficial)",
    description=DESCRIPTION,
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=str(files("easa_ad_api").joinpath("static"))), name="static")


def _when(value: str | None) -> str:
    if not value:
        return "never"
    return datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


_env = Environment(loader=PackageLoader("easa_ad_api", "templates"), autoescape=select_autoescape())
_env.filters["when"] = _when
templates = Jinja2Templates(env=_env)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = store.connect(get_settings().database_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Limit = Annotated[int, Query(ge=1, le=500, description="Page size.")]
Offset = Annotated[int, Query(ge=0, description="Rows to skip.")]


def meta(conn: sqlite3.Connection) -> dict[str, Any]:
    fresh = queries.freshness(conn, get_settings().stale_after_hours)
    return {
        "service": APP_TITLE,
        "unofficial": True,
        "notice": UNOFFICIAL_NOTICE,
        "disclaimer": DISCLAIMER,
        "source": SOURCE_NAME,
        "as_of": fresh.as_of,
        "stale": fresh.stale,
        "stale_warning": fresh.stale_warning,
        "terms": "/terms",
    }


def envelope(conn: sqlite3.Connection, data: Any, **extra: Any) -> dict[str, Any]:
    return {"meta": meta(conn), **extra, "data": data}


def _error(request: Request, status: int, detail: Any) -> JSONResponse:
    conn = store.connect(get_settings().database_path)
    try:
        body = {"meta": meta(conn), "error": detail}
    finally:
        conn.close()
    return JSONResponse(body, status_code=status)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return _error(request, exc.status_code, exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(request, 422, exc.errors())


def filters(
    type: Annotated[str | None, Query(description="Type or model designation as EASA lists it.")] = None,
    match: Annotated[
        str,
        Query(
            pattern="^(exact|contains)$",
            description="exact: equal to a listed type or model (case-insensitive)."
            " contains: substring of the type text as EASA published it.",
        ),
    ] = "exact",
    holder: Annotated[str | None, Query(description="Approval holder, substring.")] = None,
    issuer: Annotated[
        str | None, Query(description="EASA, US (FAA), CF (TCCA), BR (ANAC), F, G, ...")
    ] = None,
    pub_class: Annotated[
        str | None, Query(alias="class", description="AD, EAD (emergency), PAD, ADR, SIB.")
    ] = None,
    status: Annotated[
        str | None,
        Query(description="active, superseded, revised, cancelled, withdrawn, not_adopted, proposed."),
    ] = None,
    q: Annotated[str | None, Query(description="Text in the subject.")] = None,
    effective_from: Annotated[str | None, Query(description="YYYY-MM-DD")] = None,
    effective_to: Annotated[str | None, Query(description="YYYY-MM-DD")] = None,
    issued_from: Annotated[str | None, Query(description="YYYY-MM-DD")] = None,
    issued_to: Annotated[str | None, Query(description="YYYY-MM-DD")] = None,
) -> queries.Filters:
    return queries.Filters(
        type=type,
        match=match,
        holder=holder,
        issuer=issuer,
        pub_class=pub_class,
        status=status,
        q=q,
        effective_from=effective_from,
        effective_to=effective_to,
        issued_from=issued_from,
        issued_to=issued_to,
    )


Filt = Annotated[queries.Filters, Depends(filters)]


@app.get("/v1/ads", summary="Search and filter ADs")
def list_ads(conn: Conn, f: Filt, limit: Limit = 50, offset: Offset = 0) -> dict[str, Any]:
    total, rows = queries.search(conn, f, limit, offset)
    return envelope(conn, rows, total=total, limit=limit, offset=offset)


@app.get("/v1/ads/{number}", summary="One AD with documents, supersedure chain and status")
def get_ad(conn: Conn, number: str) -> dict[str, Any]:
    data = queries.get_publication(conn, number.strip().upper())
    if data is None:
        raise HTTPException(404, f"No publication {number!r} in this mirror.")
    return envelope(conn, data)


@app.get("/v1/types", summary="Type designations with AD counts")
def list_types(
    conn: Conn,
    q: Annotated[str | None, Query(description="Substring of the type designation.")] = None,
    holder: Annotated[str | None, Query(description="Approval holder, substring.")] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict[str, Any]:
    total, rows = queries.list_types(conn, q, holder, limit, offset)
    return envelope(conn, rows, total=total, limit=limit, offset=offset)


@app.get("/v1/types/{type_name}/ads", summary="All ADs listed for one type or model")
def type_ads(conn: Conn, type_name: str, f: Filt, limit: Limit = 100, offset: Offset = 0) -> dict[str, Any]:
    f.type = type_name
    total, rows = queries.search(conn, f, limit, offset)
    return envelope(conn, rows, type=type_name, total=total, limit=limit, offset=offset)


ChangeType = Annotated[str | None, Query(description=", ".join(queries.CHANGE_TYPES))]


@app.get("/v1/changes", summary="Change feed (paged by id)")
def list_changes(
    conn: Conn,
    f: Filt,
    since: Annotated[str | None, Query(description="ISO time; events observed at or after it.")] = None,
    after_id: Annotated[int | None, Query(description="Cursor: the last id you have.")] = None,
    include_imported: Annotated[bool, Query(description="Include the initial archive import.")] = False,
    change_type: ChangeType = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    rows = queries.changes(
        conn,
        f,
        since=since,
        after_id=after_id,
        include_imported=include_imported,
        limit=limit,
        change_type=change_type,
    )
    next_after = rows[-1]["id"] if len(rows) == limit else None
    return envelope(conn, rows, next_after_id=next_after)


def _entry_title(change: dict[str, Any]) -> str:
    pub = change["publication"]
    prefix = "[EMERGENCY AD] " if pub["emergency"] else ""
    kind = {
        "new": "New",
        "status_changed": f"Status {change['old_value']} -> {change['new_value']}",
        "effective_date_changed": "Effective date changed",
        "subject_changed": "Subject changed",
        "issue_date_changed": "Issue date changed",
        "document_added": "Document added",
    }.get(change["change_type"], change["change_type"])
    return f"{prefix}{pub['class']} {pub['number']}: {kind}. {pub['subject']}"


def _atom(request: Request, conn: sqlite3.Connection, changes: list[dict[str, Any]]) -> bytes:
    ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", ns)
    m = meta(conn)
    feed = ET.Element(f"{{{ns}}}feed")
    query = f"?{request.url.query}" if request.url.query else ""
    ET.SubElement(feed, f"{{{ns}}}id").text = f"tag:easa-ad-api,2026:changes{query}"
    ET.SubElement(feed, f"{{{ns}}}title").text = f"{APP_TITLE} (unofficial): AD changes{query}"
    subtitle = f"{UNOFFICIAL_NOTICE} {DISCLAIMER}"
    if m["stale_warning"]:
        subtitle += f" {m['stale_warning']}"
    ET.SubElement(feed, f"{{{ns}}}subtitle").text = subtitle
    ET.SubElement(feed, f"{{{ns}}}updated").text = (
        changes[0]["observed_at"] if changes else m["as_of"] or datetime.now(UTC).isoformat()
    )
    ET.SubElement(feed, f"{{{ns}}}link", rel="self", href=str(request.url))
    ET.SubElement(feed, f"{{{ns}}}rights").text = f"Data: EASA, {EASA_COPYRIGHT_URL}"
    author = ET.SubElement(feed, f"{{{ns}}}author")
    ET.SubElement(author, f"{{{ns}}}name").text = f"{APP_TITLE} (unofficial mirror)"
    for change in changes:
        pub = change["publication"]
        entry = ET.SubElement(feed, f"{{{ns}}}entry")
        ET.SubElement(entry, f"{{{ns}}}id").text = f"tag:easa-ad-api,2026:change/{change['id']}"
        ET.SubElement(entry, f"{{{ns}}}title").text = _entry_title(change)
        ET.SubElement(entry, f"{{{ns}}}updated").text = change["observed_at"]
        if pub["emergency"]:
            ET.SubElement(entry, f"{{{ns}}}category", term="emergency", label="Emergency AD")
        ET.SubElement(entry, f"{{{ns}}}category", term=pub["class"])
        docs = pub["official_documents"]
        href = docs[0]["url"] if docs else pub["official_source_page"]
        ET.SubElement(entry, f"{{{ns}}}link", rel="alternate", href=href)
        ET.SubElement(entry, f"{{{ns}}}link", rel="related", href=pub["official_source_page"])
        types = "; ".join(t["type"] for t in pub["types"]) or pub["types_text"]
        lines = [
            f"{UNOFFICIAL_NOTICE} {DISCLAIMER}",
            f"{pub['number']} ({pub['class']}, issuer {pub['issuer']}): {pub['subject']}",
            f"Holder: {', '.join(pub['holders'])}. Types: {types}.",
            f"Issued {pub['issue_date']}, effective {pub['effective_date'] or 'not listed'}."
            f" Status in this mirror: {pub['status']}.",
            "Official documents: " + (", ".join(d["url"] for d in docs) or "none listed"),
            f"Official EASA page: {pub['official_source_page']}",
        ]
        ET.SubElement(entry, f"{{{ns}}}content", type="text").text = "\n".join(lines)
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


@app.get(
    "/v1/changes.atom",
    summary="Atom feed of changes; filter per type, holder, class or issuer",
    response_class=Response,
)
def changes_atom(
    request: Request,
    conn: Conn,
    f: Filt,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Response:
    rows = queries.changes(
        conn, f, since=None, after_id=None, include_imported=False, limit=limit, newest_first=True
    )
    return Response(_atom(request, conn, rows), media_type="application/atom+xml")


@app.get("/v1/status", summary="Last sync per source, stale flag, counts, reconciliation")
def status(conn: Conn) -> dict[str, Any]:
    settings = get_settings()
    fresh = queries.freshness(conn, settings.stale_after_hours)
    data = {
        "stale_after_hours": settings.stale_after_hours,
        "stale": fresh.stale,
        "stale_warning": fresh.stale_warning,
        "sources": fresh.sources,
        "source_pages": {"interim": INTERIM_URL, "archive": ARCHIVE_URL, "easa_tool": EASA_TOOL_URL},
        "counts": queries.counts(conn),
        "csv_reconciliation": queries.reconciliation(conn),
    }
    return envelope(conn, data)


EXAMPLE_TYPE = "A350"


def _page_context(request: Request, conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "base": str(request.base_url).rstrip("/"),
        "stale_hours": get_settings().stale_after_hours,
        "title": APP_TITLE,
        "notice": UNOFFICIAL_NOTICE,
        "disclaimer": DISCLAIMER,
        "meta": meta(conn),
        "status": queries.freshness(conn, get_settings().stale_after_hours),
        "interim_url": INTERIM_URL,
        "archive_url": ARCHIVE_URL,
        "tool_url": EASA_TOOL_URL,
        "faq_url": EASA_FAQ_URL,
        "copyright_url": EASA_COPYRIGHT_URL,
        "repo_url": REPO_URL,
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing(
    request: Request,
    conn: Conn,
    type: Annotated[str | None, Query(max_length=80)] = None,
) -> HTMLResponse:
    example_type = (type or "").strip() or EXAMPLE_TYPE
    total, example = queries.search(conn, queries.Filters(type=example_type), 8, 0)
    context = _page_context(request, conn)
    context |= {
        "example_type": example_type,
        "example_total": total,
        "example": example,
        "example_url": f"{context['base']}/v1/types/{quote(example_type, safe='')}/ads",
        "feed_url": f"{context['base']}/v1/changes.atom?{urlencode({'type': example_type})}",
        "counts": queries.counts(conn),
    }
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms(request: Request, conn: Conn) -> HTMLResponse:
    return templates.TemplateResponse(request, "terms.html", _page_context(request, conn))
