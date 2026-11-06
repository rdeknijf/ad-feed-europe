"""Parsers for the EASA source pages. Pure functions: text in, records out."""

import csv
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

from lxml import html

from easa_ad_api.config import EASA_BASE
from easa_ad_api.numbers import INTERIM_ISSUERS, classify, normalize_number

PLACEHOLDER_YEAR = 1980


class SourceFormatError(ValueError):
    """The source page no longer has the structure the parser expects."""


@dataclass
class TypeEntry:
    holder: str
    type_name: str
    models: list[str] = field(default_factory=list)
    confidence: str = "high"


@dataclass
class Document:
    url: str
    label: str
    kind: str


@dataclass
class Record:
    """One row from one source, normalized to the same fields."""

    number: str
    source: str
    pub_class: str
    issuer: str
    issue_date: str | None
    issue_date_uncertain: bool
    effective_date: str | None
    subject: str
    holders: list[str]
    types_raw: str
    types: list[TypeEntry]
    documents: list[Document]

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _absolute(url: str) -> str:
    return urljoin(EASA_BASE + "/", url.strip())


def _doc_kind(url: str, label: str) -> str:
    probe = f"{url} {label}".lower()
    if ".zip" in probe or "zip" in label.lower():
        return "zip"
    return "pdf"


def parse_us_date(value: str) -> tuple[str | None, bool]:
    """Archive dates are MM/DD/YYYY. Returns (ISO date, uncertain flag)."""
    value = value.strip()
    if not value:
        return None, False
    parsed = datetime.strptime(value, "%m/%d/%Y").date()
    return parsed.isoformat(), parsed.year == PLACEHOLDER_YEAR


def parse_iso_date(value: str) -> str | None:
    value = _clean(value)
    if not value:
        return None
    return date.fromisoformat(value).isoformat()


# Archive page -------------------------------------------------------------

_DRUPAL_SETTINGS = re.compile(
    r'<script type="application/json" data-drupal-selector="drupal-settings-json">(.*?)</script>',
    re.S,
)
ARCHIVE_HEADER = [
    "AD class",
    "Class number",
    "Documents",
    "Issue Date",
    "Subject",
    "Approval Holder / Type Designation",
    "Effective date",
]


def archive_rows(page: str) -> list[list[str]]:
    """Extract the raw rows (header included) from the archive page's Drupal JSON."""
    m = _DRUPAL_SETTINGS.search(page)
    if not m:
        raise SourceFormatError("archive page: drupal-settings-json block not found")
    try:
        data = json.loads(m.group(1))["easa_datasets"]["data"]
    except KeyError, json.JSONDecodeError:
        raise SourceFormatError("archive page: easa_datasets.data missing") from None
    if not data or data[0] != ARCHIVE_HEADER:
        raise SourceFormatError(f"archive page: unexpected header {data[0] if data else None}")
    return data


def split_archive_types(holder_field: str) -> tuple[list[str], str, list[TypeEntry]]:
    """Split 'HOLDER\\nTYPE/model/model' into holders, raw type text and parsed entries.

    Model names can contain '/', so the split is a guess: confidence is 'low'.
    """
    lines = [_clean(line) for line in holder_field.split("\n")]
    holder = lines[0] if lines else ""
    raw = " ".join(line for line in lines[1:] if line)
    if not raw:
        return [holder] if holder else [], "", []
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    entry = TypeEntry(holder=holder, type_name=parts[0], models=parts[1:], confidence="low")
    return [holder] if holder else [], raw, [entry]


def parse_archive(page: str) -> list[Record]:
    records = []
    for row in archive_rows(page)[1:]:
        ad_class, number, documents, issued, subject, holder_field, effective = row
        number = normalize_number(number)
        info = classify(number, ad_class.strip() or None)
        issue_date, uncertain = parse_us_date(issued)
        effective_date, _ = parse_us_date(effective)
        holders, raw, types = split_archive_types(holder_field)
        docs = []
        if documents.strip():
            for a in html.fragment_fromstring(documents, create_parent="div").iter("a"):
                href = a.get("href")
                if href:
                    label = _clean(a.text_content())
                    docs.append(Document(_absolute(href), label, _doc_kind(href, label)))
        records.append(
            Record(
                number=number,
                source="archive",
                pub_class=info.pub_class,
                issuer=info.issuer,
                issue_date=issue_date,
                issue_date_uncertain=uncertain,
                effective_date=effective_date,
                subject=_clean(subject),
                holders=holders,
                types_raw=raw,
                types=types,
                documents=docs,
            )
        )
    return records


# Interim page -------------------------------------------------------------

INTERIM_HEADER = [
    "Number",
    "Issued by",
    "Issue date",
    "Subject",
    "Approval Holder / Type Designation",
    "Effective date",
    "Attachment",
]


@dataclass
class CsvPeriod:
    period: int
    date_from: str
    date_to: str
    csv_url: str


def _own_text(li: html.HtmlElement) -> str:
    return _clean("".join(li.xpath("./text()")))


def _types_from_list(ul: html.HtmlElement, holder: str) -> list[TypeEntry]:
    entries = []
    for li in ul.xpath("./li"):
        models = [_own_text(sub) for sub in li.xpath("./ul/li")]
        entries.append(TypeEntry(holder=holder, type_name=_own_text(li), models=models))
    return entries


def parse_holder_cell(td: html.HtmlElement) -> tuple[list[str], list[TypeEntry]]:
    """Read holder and type tree from an interim cell.

    Two layouts exist: '<p>HOLDER</p><ul>types</ul>' (newer rows) and
    '<ul><li>HOLDER<ul>types<ul>models</ul></ul></li></ul>' (older rows).
    """
    holders: list[str] = []
    types: list[TypeEntry] = []
    paragraphs = [p for p in td.xpath("./p") if _clean(p.text_content())]
    if paragraphs:
        for p in paragraphs:
            holder = _clean(p.text_content())
            holders.append(holder)
            ul = p.getnext()
            while ul is not None and ul.tag != "ul":
                if ul.tag == "p" and _clean(ul.text_content()):
                    ul = None
                    break
                ul = ul.getnext()
            if ul is not None:
                types.extend(_types_from_list(ul, holder))
        return holders, types
    for li in td.xpath("./ul/li"):
        holder = _own_text(li)
        holders.append(holder)
        for sub in li.xpath("./ul"):
            types.extend(_types_from_list(sub, holder))
    return holders, types


def _subject_cell(td: html.HtmlElement) -> tuple[str, str | None]:
    """Subject text without the obfuscated comment-email link, plus the class icon hint."""
    for span in td.xpath('.//span[contains(@class, "spamspan")]'):
        span.drop_tree()
    hint = None
    for src in td.xpath(".//img/@src"):
        if m := re.search(r"adclass_([a-z]+)\.", src):
            hint = m.group(1).upper()
    return _clean(td.text_content()), hint


def _types_raw(types: list[TypeEntry]) -> str:
    parts = []
    for t in types:
        parts.append(t.type_name + (": " + ", ".join(t.models) if t.models else ""))
    return "; ".join(parts)


def parse_interim(page: str) -> tuple[list[Record], list[CsvPeriod]]:
    doc = html.document_fromstring(page)
    tables = doc.xpath('//table[contains(concat(" ", @class, " "), " ad-list ")]')
    if len(tables) != 1:
        raise SourceFormatError(f"interim page: expected one ad-list table, found {len(tables)}")
    header = [_clean(th.text_content()) for th in tables[0].xpath("./thead/tr/th")]
    if header != INTERIM_HEADER:
        raise SourceFormatError(f"interim page: unexpected header {header}")
    records = []
    for tr in tables[0].xpath("./tbody/tr"):
        tds = tr.xpath("./td")
        if len(tds) != len(INTERIM_HEADER):
            raise SourceFormatError(f"interim page: row with {len(tds)} cells")
        number = normalize_number(tds[0].text_content())
        if not number:
            continue
        issued_by = _clean(tds[1].text_content()).upper()
        subject, class_hint = _subject_cell(tds[3])
        info = classify(number, class_hint if class_hint in ("AD", "EAD", "PAD", "ADR") else None)
        holders, types = parse_holder_cell(tds[4])
        docs = []
        for a in tds[6].xpath(".//a"):
            href = a.get("href")
            if href:
                label = _clean(a.text_content())
                docs.append(Document(_absolute(href), label, _doc_kind(href, label)))
        records.append(
            Record(
                number=number,
                source="interim",
                pub_class=info.pub_class,
                issuer=INTERIM_ISSUERS.get(issued_by, info.issuer),
                issue_date=parse_iso_date(tds[2].text_content()),
                issue_date_uncertain=False,
                effective_date=parse_iso_date(tds[5].text_content()),
                subject=subject,
                holders=holders,
                types_raw=_types_raw(types),
                types=types,
                documents=docs,
            )
        )
    return records, parse_csv_periods(doc)


def parse_csv_periods(doc: html.HtmlElement) -> list[CsvPeriod]:
    periods = []
    for table in doc.xpath("//table"):
        header = [_clean(th.text_content()) for th in table.xpath("./thead/tr/th")]
        if header[:4] != ["Biweekly", "From", "To", "CSV"]:
            continue
        for tr in table.xpath("./tbody/tr"):
            tds = tr.xpath("./td")
            links = tds[3].xpath(".//a/@href")
            if len(tds) < 4 or not links:
                continue
            periods.append(
                CsvPeriod(
                    period=int(_clean(tds[0].text_content())),
                    date_from=parse_iso_date(tds[1].text_content()) or "",
                    date_to=parse_iso_date(tds[2].text_content()) or "",
                    csv_url=_absolute(links[0]),
                )
            )
    return periods


# Biweekly CSV -------------------------------------------------------------


def parse_csv_zip(blob: bytes) -> list[Record]:
    """Read the one semicolon-separated CSV in a biweekly zip (no header, five fields)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise SourceFormatError(f"csv zip: expected one .csv, found {names}")
        raw = zf.read(names[0])
    return parse_csv_text(raw.decode("utf-8-sig"))


def parse_csv_text(text: str) -> list[Record]:
    records = []
    for row in csv.reader(io.StringIO(text), delimiter=";", quotechar='"'):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != 5:
            raise SourceFormatError(f"csv: expected 5 fields, got {len(row)}: {row}")
        number, issued, holder, type_name, subject = (_clean(c) for c in row)
        number = normalize_number(number)
        info = classify(number)
        types = [TypeEntry(holder=holder, type_name=type_name)] if type_name else []
        records.append(
            Record(
                number=number,
                source="csv",
                pub_class=info.pub_class,
                issuer=info.issuer,
                issue_date=parse_iso_date(issued),
                issue_date_uncertain=False,
                effective_date=None,
                subject=subject,
                holders=[holder] if holder else [],
                types_raw=type_name,
                types=types,
                documents=[],
            )
        )
    return records
