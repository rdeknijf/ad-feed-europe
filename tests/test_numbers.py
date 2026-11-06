import pytest

from easa_ad_api.numbers import (
    classify,
    normalize_issuer,
    normalize_number,
    resolve_reference,
    status_from_subject,
    superseded_by_reference,
)


@pytest.mark.parametrize(
    ("number", "archive_class", "issuer", "pub_class", "base", "revision", "emergency"),
    [
        ("2026-0199", None, "EASA", "AD", "2026-0199", 0, False),
        ("2026-0191-E", None, "EASA", "EAD", "2026-0191-E", 0, True),
        ("2020-0012R1-E", None, "EASA", "EAD", "2020-0012-E", 1, True),
        ("2026-0177R1", None, "EASA", "AD", "2026-0177", 1, False),
        ("2026-0160", "ADR", "EASA", "ADR", "2026-0160", 0, False),
        ("26-133", None, "EASA", "PAD", "26-133", 0, False),
        ("2008-0012-CN", None, "EASA", "AD", "2008-0012-CN", 0, False),
        ("US-2026-20-14", None, "US", "AD", "US-2026-20-14", 0, False),
        ("CF-2026-47R1", None, "CF", "AD", "CF-2026-47", 1, False),
        ("BR-2026-10-01", None, "BR", "AD", "BR-2026-10-01", 0, False),
        ("F-1980-251-043", None, "F", "AD", "F-1980-251-043", 0, False),
        ("G-2005-PRE12", None, "G", "AD", "G-2005-PRE12", 0, False),
        ("DCA-1XL-3", None, "DCA", "AD", "DCA-1XL-3", 0, False),
        ("SE-1990-12-Rev-A", None, "SE", "AD", "SE-1990-12-Rev-A", 0, False),
        ("ISR-I-28-12-01-03", None, "ISR", "AD", "ISR-I-28-12-01-03", 0, False),
        ("SAIB-2026-17", None, "US", "SIB", "SAIB-2026-17", 0, False),
        ("1999-01-02-03", None, "UNKNOWN", "AD", "1999-01-02-03", 0, False),
        ("CF-2026-48", "EAD", "CF", "EAD", "CF-2026-48", 0, True),
    ],
)
def test_classify(
    number: str,
    archive_class: str | None,
    issuer: str,
    pub_class: str,
    base: str,
    revision: int,
    emergency: bool,
) -> None:
    info = classify(number, archive_class)
    assert (info.issuer, info.pub_class, info.base_number, info.revision, info.emergency) == (
        issuer,
        pub_class,
        base,
        revision,
        emergency,
    )


def test_normalize_number_strips_markup_spaces() -> None:
    assert normalize_number("2026-0213\xa0\xa0\xa0 ") == "2026-0213"


@pytest.mark.parametrize(("value", "expected"), [("FAA", "US"), ("tcca", "CF"), ("EU", "EASA"), ("F", "F")])
def test_normalize_issuer(value: str, expected: str) -> None:
    assert normalize_issuer(value) == expected


@pytest.mark.parametrize(
    ("subject", "status"),
    [
        ("SUPERSEDED BY AD 2026-0190", "superseded"),
        ("CANCELLED: Engine – Blades – Inspection", "cancelled"),
        ("CANCELLATION NOTICE - SUPERSEDED BY DGAC FRANCE AD F-2005-123", "cancelled"),
        ("WITHDRAWN - EXPLANATORY STATEMENT ATTACHED", "withdrawn"),
        ("NOT ADOPTED - EASA DECISION ATTACHED", "not_adopted"),
        ("NOT ELIGIBLE FOR ADOPTION - EASA POSITION ATTACHED", "not_adopted"),
        ("SUSPENSION OF FLIGHT OPERATIONS", None),
        ("Landing Gear – Main Landing Gear Barrel – Inspection", None),
    ],
)
def test_status_from_subject(subject: str, status: str | None) -> None:
    assert status_from_subject(subject) == status


@pytest.mark.parametrize(
    ("subject", "resolved"),
    [
        ("SUPERSEDED BY AD 2026-0190", "2026-0190"),
        ("SUPERSEDED BY EASA AD 2026-0174", "2026-0174"),
        ("SUPERSEDED BY EASA EMERGENCY AD 2006-0123-E", "2006-0123-E"),
        ("SUPERSEDED BY FAA AD-2026-14-11", "US-2026-14-11"),
        ("SUPERSEDED BY FAA AD 2025-03-04", "US-2025-03-04"),
        ("SUPERSEDED BY ANAC BRAZIL AD 2026-07-02", "BR-2026-07-02"),
        ("SUPERSEDED BY TCCA AD CF-2026-48", "CF-2026-48"),
        ("SUPERSEDED BY DGAC FRANCE AD F-2004-123", "F-2004-123"),
        ("SUPERSEDED BY DGAC FRANCE AD 2003-123", "F-2003-123"),
        ("SUPERSEDED BY CAA UK AD G-2005-0012", "G-2005-0012"),
        ("SUPERSEDED BY EASA AD 2026-0015R1", "2026-0015R1"),
    ],
)
def test_superseded_by_reference(subject: str, resolved: str) -> None:
    raw, number = superseded_by_reference(subject)
    assert raw
    assert number == resolved


def test_superseded_by_reference_absent() -> None:
    assert superseded_by_reference("Fuel – Hoses – Inspection") == (None, None)
    assert resolve_reference("see the EASA website") is None
