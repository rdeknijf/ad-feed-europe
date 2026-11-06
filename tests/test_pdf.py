from pathlib import Path

import pytest

from easa_ad_api.pdf import header_fields, parse_header

PDF_TEXT = Path(__file__).parent / "fixtures" / "pdf-text"


# Golden values checked against the header text of the real EASA PDFs (fetched 2026-11-04).
@pytest.mark.parametrize(
    ("number", "issued", "effective", "supersedes", "revises", "foreign", "applicability_start"),
    [
        ("2026-0199", "2026-10-20", "2026-11-03", ["2026-0086"], [], None, 'SZD-54-2 "Perkoz" sailplanes'),
        ("2026-0203", "2026-10-27", "2026-11-10", ["2025-0206R1"], [], None, "Trent 1000-AE3"),
        ("2026-0015R1", "2026-10-30", "2026-11-06", [], ["2026-0015-E"], None, "AS 332 C, AS 332 C1"),
        (
            "2026-0213",
            "2026-11-04",
            "2026-11-18",
            [],
            [],
            "Transport Canda Civil Aviation (TCCA) AD CF-2026-16",
            "ACLP BD-500-1A10 aeroplanes",
        ),
        ("2026-0212", "2026-11-04", "2026-11-18", [], [], None, "DAHER Aircraft TBM 700"),
        ("2026-0211", "2026-11-04", "2026-11-18", ["2025-0231"], [], None, "SA 330 J helicopters"),
        ("2025-0159R1", "2026-10-19", "2026-10-26", [], ["2025-0159"], None, "AS 350 B, AS 350 BA"),
        ("2026-0206", "2026-10-30", "2026-11-13", ["2024-0205"], [], None, "EC 225 LP helicopters"),
        ("2020-0271R1", "2026-10-08", "2026-10-15", [], ["2020-0271"], None, "AB139, AW139 and AW189"),
        ("2026-0200", "2026-10-20", "2026-11-03", [], [], None, "Airbus A330-841 and A330-941"),
    ],
)
def test_parse_header_golden(
    number: str,
    issued: str,
    effective: str,
    supersedes: list[str],
    revises: list[str],
    foreign: str | None,
    applicability_start: str,
) -> None:
    fields = parse_header((PDF_TEXT / f"{number}.txt").read_text())
    assert fields.issue_date == issued
    assert fields.effective_date == effective
    assert fields.supersedes == supersedes
    assert fields.revises == revises
    assert fields.foreign_ad == foreign
    assert fields.applicability is not None
    assert fields.applicability.startswith(applicability_start)
    assert len(fields.applicability) <= 300


def test_supersedure_text_kept_short() -> None:
    fields = parse_header((PDF_TEXT / "2026-0199.txt").read_text())
    assert fields.supersedure == "This AD supersedes EASA AD 2026-0086 dated 30 April 2026."


def test_header_fields_only_first_page() -> None:
    text = "Applicability: first page value.\n\fApplicability: second page value.\n"
    assert header_fields(text)["applicability"] == "first page value."


def test_parse_header_empty_text() -> None:
    fields = parse_header("")
    assert fields.issue_date is None
    assert fields.supersedes == []
