"""Read a few short factual fields from the header of an EASA AD PDF (pdftotext -layout).

Only these fields are kept for serving: issue date, effective date, the supersedure and
revision references, the foreign AD reference and the first line of the applicability.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

# A header label: a word group at the start of a line, an optional "(s)" or "'s Name", a colon.
_LABEL_LINE = re.compile(r"^\s*(?P<label>[A-Z][A-Za-z/.’' ]{1,40}?)(?:\(s\))?\s*:(?P<rest>.*)$")
_DATE = re.compile(
    r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b"
)
_AD_REF = re.compile(
    r"\b(?:EASA\s+)?(?:Emergency\s+)?(?:AD|ADR)\s+(?P<num>\d{4}-\d{4}(?:R\d+)?(?:-E)?)", re.I
)


@dataclass
class HeaderFields:
    issue_date: str | None = None
    effective_date: str | None = None
    supersedure: str | None = None
    supersedes: list[str] = field(default_factory=list)
    revision: str | None = None
    revises: list[str] = field(default_factory=list)
    foreign_ad: str | None = None
    applicability: str | None = None


def _iso(text: str) -> str | None:
    m = _DATE.search(text)
    if not m:
        return None
    return datetime.strptime(re.sub(r"\s+", " ", m.group(1)), "%d %B %Y").date().isoformat()


def header_fields(text: str) -> dict[str, str]:
    """Map label -> value for 'Label: value' blocks in the first page.

    A value runs on over indented lines until a blank line or the next label.
    """
    first_page = text.split("\f", 1)[0]
    result: dict[str, str] = {}
    current: str | None = None
    for line in first_page.splitlines():
        if not line.strip():
            current = None
            continue
        m = _LABEL_LINE.match(line)
        if m:
            label = re.sub(r"[’']s Name$", "", re.sub(r"\s+", " ", m["label"]).strip())
            current = label.casefold()
            result.setdefault(current, "")
            result[current] = (result[current] + " " + m["rest"].strip()).strip()
        elif current is not None:
            result[current] = (result[current] + " " + line.strip()).strip()
    return {k: re.sub(r"\s+", " ", v).strip() for k, v in result.items()}


def _none_if_empty(value: str | None) -> str | None:
    if value is None or not value.strip() or value.strip().lower() in ("none", "not applicable"):
        return None
    return value.strip()


def parse_header(text: str) -> HeaderFields:
    fields = header_fields(text)
    out = HeaderFields()
    if issued := fields.get("issued"):
        out.issue_date = _iso(issued)
    if effective := fields.get("effective date"):
        out.effective_date = _iso(effective)
    if sup := _none_if_empty(fields.get("supersedure")):
        out.supersedure = sup[:300]
        out.supersedes = list(dict.fromkeys(m["num"].upper() for m in _AD_REF.finditer(sup)))
    revision = fields.get("revision") or fields.get("revison")
    if rev := _none_if_empty(revision):
        out.revision = rev[:300]
        out.revises = list(dict.fromkeys(m["num"].upper() for m in _AD_REF.finditer(rev)))
    if foreign := _none_if_empty(fields.get("foreign ad")):
        out.foreign_ad = foreign[:200]
    if app := fields.get("applicability"):
        out.applicability = app[:300]
    return out
