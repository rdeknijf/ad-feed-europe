"""Classify publication numbers and read status signals from subjects."""

import re
from dataclasses import dataclass

# Prefix of the number -> issuer code. Codes without a known authority name stay codes.
ISSUER_NAMES = {
    "EASA": "European Union Aviation Safety Agency",
    "US": "Federal Aviation Administration (United States)",
    "CF": "Transport Canada Civil Aviation",
    "BR": "ANAC (Brazil)",
    "F": "DGAC (France), legacy national AD",
    "G": "CAA (United Kingdom), legacy national AD",
}
ISSUER_ALIASES = {
    "EU": "EASA",
    "FAA": "US",
    "CA": "CF",
    "TCCA": "CF",
    "ANAC": "BR",
}
# Issued-by column of the interim page -> issuer code.
INTERIM_ISSUERS = {"EU": "EASA", "US": "US", "CA": "CF", "BR": "BR"}

_EASA_AD = re.compile(r"^(?P<base>\d{4}-\d{4})(?:R(?P<rev>\d+))?(?P<e>-E)?$")
_EASA_CN = re.compile(r"^\d{4}-\d{4}-CN$")
_EASA_PAD = re.compile(r"^(?P<base>\d{2}-\d{3})(?:R(?P<rev>\d+))?$")
_FOREIGN = re.compile(r"^(?P<prefix>[A-Z]{1,4})-(?P<rest>.+)$")
_REVISION = re.compile(r"^(?P<base>.+?)R(?P<rev>\d+)$")


@dataclass(frozen=True)
class NumberInfo:
    number: str
    base_number: str
    revision: int
    issuer: str
    pub_class: str
    emergency: bool


def normalize_number(raw: str) -> str:
    """Strip spaces, non-breaking spaces and markup leftovers from a number cell."""
    return re.sub(r"\s+", "", raw.replace("\xa0", " ")).strip().upper()


def normalize_issuer(value: str) -> str:
    code = value.strip().upper()
    return ISSUER_ALIASES.get(code, code)


def classify(number: str, archive_class: str | None = None) -> NumberInfo:
    """Derive issuer, class, revision and emergency flag from a publication number.

    The archive gives the class directly; the interim page does not, so the class
    is derived from the number shape there.
    """
    if m := _EASA_AD.match(number):
        emergency = bool(m["e"])
        derived = "EAD" if emergency else "AD"
        return NumberInfo(
            number=number,
            base_number=m["base"] + ("-E" if emergency else ""),
            revision=int(m["rev"] or 0),
            issuer="EASA",
            pub_class=archive_class or derived,
            emergency=emergency or archive_class == "EAD",
        )
    if _EASA_CN.match(number):
        return NumberInfo(number, number, 0, "EASA", archive_class or "AD", False)
    if m := _EASA_PAD.match(number):
        return NumberInfo(number, m["base"], int(m["rev"] or 0), "EASA", archive_class or "PAD", False)
    if number.startswith("SAIB-"):
        return NumberInfo(number, number, 0, "US", archive_class or "SIB", False)
    if m := _FOREIGN.match(number):
        rev = _REVISION.match(number)
        return NumberInfo(
            number=number,
            base_number=rev["base"] if rev else number,
            revision=int(rev["rev"]) if rev else 0,
            issuer=m["prefix"],
            pub_class=archive_class or "AD",
            emergency=archive_class == "EAD",
        )
    return NumberInfo(number, number, 0, "UNKNOWN", archive_class or "AD", False)


# Status signals that EASA writes into the Subject of an existing row.
_STATUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("superseded", re.compile(r"^SUPERSEDED\b", re.I)),
    ("cancelled", re.compile(r"^(CANCELLED|CANCELLATION NOTICE)\b", re.I)),
    ("withdrawn", re.compile(r"^WITHDRAWN\b", re.I)),
    ("not_adopted", re.compile(r"^NOT (ADOPTED|ELIGIBLE FOR ADOPTION)\b", re.I)),
]


def status_from_subject(subject: str) -> str | None:
    for status, pattern in _STATUS_PATTERNS:
        if pattern.match(subject.strip()):
            return status
    return None


# "SUPERSEDED BY <authority words> <number>" -> canonical number.
_REFERENCE_PREFIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bFAA\b", re.I), "US-"),
    (re.compile(r"\bANAC\b", re.I), "BR-"),
    (re.compile(r"\b(TCCA|TRANSPORT CANADA)\b", re.I), "CF-"),
    (re.compile(r"\bDGAC\b", re.I), "F-"),
    (re.compile(r"\bCAA UK\b", re.I), "G-"),
]
_SUPERSEDED_BY = re.compile(r"SUPERSEDED\s+BY\s+(?P<ref>.+)$", re.I)
_REF_NUMBER = re.compile(r"(?P<num>(?:[A-Z]{1,4}-)?\d{2,4}-\d{2,4}(?:-\d{1,4})?(?:R\d+)?(?:-E)?)\b", re.I)


def resolve_reference(text: str) -> str | None:
    """Map a free-text AD reference such as 'FAA AD 2026-14-11' to a stored number."""
    m = _REF_NUMBER.search(text.replace("AD-", "AD "))
    if not m:
        return None
    num = m["num"].upper()
    if re.match(r"^[A-Z]", num):
        return num
    for pattern, prefix in _REFERENCE_PREFIXES:
        if pattern.search(text):
            return prefix + num
    return num


def superseded_by_reference(subject: str) -> tuple[str | None, str | None]:
    """Return (raw reference, resolved number) for a 'SUPERSEDED BY ...' subject."""
    m = _SUPERSEDED_BY.search(subject.strip())
    if not m:
        return None, None
    raw = m["ref"].strip()
    return raw, resolve_reference(raw)
