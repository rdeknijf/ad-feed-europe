import io
import json
import zipfile
from collections.abc import Callable

import pytest
from lxml import html

from easa_ad_api.parsers import (
    SourceFormatError,
    parse_archive,
    parse_csv_text,
    parse_csv_zip,
    parse_holder_cell,
    parse_interim,
    parse_us_date,
    split_archive_types,
)


def by_number(records: list) -> dict:
    return {r.number: r for r in records}


def test_archive_rows_and_fields(archive_page: str) -> None:
    records = by_number(parse_archive(archive_page))
    assert len(records) == 44
    ad = records["2026-0203"]
    assert ad.pub_class == "AD"
    assert ad.issuer == "EASA"
    assert ad.issue_date == "2026-10-27"
    assert ad.effective_date == "2026-11-10"
    assert ad.holders == ["ROLLS-ROYCE DEUTSCHLAND Ltd & Co KG"]
    assert ad.types[0].type_name == "TRENT 1000"
    assert ad.types[0].confidence == "low"
    assert [d.kind for d in ad.documents] == ["pdf", "zip"]
    assert ad.documents[0].url == "https://www.easa.europa.eu/download/AD-tmp-storage/EASA_AD_2026_0203.pdf"


def test_archive_placeholder_year_is_flagged(archive_page: str) -> None:
    record = by_number(parse_archive(archive_page))["F-1980-251-043"]
    assert record.issue_date == "1980-12-31"
    assert record.issue_date_uncertain is True


def test_archive_emergency_class(archive_page: str) -> None:
    record = by_number(parse_archive(archive_page))["2026-0191-E"]
    assert record.pub_class == "EAD"
    assert record.subject == "SUSPENSION OF FLIGHT OPERATIONS"


def test_archive_missing_effective_date(archive_page: str) -> None:
    assert by_number(parse_archive(archive_page))["26-130"].effective_date is None


@pytest.mark.parametrize(
    "page",
    [
        "<html>no settings</html>",
        '<script type="application/json" data-drupal-selector="drupal-settings-json">{"x": 1}</script>',
        '<script type="application/json" data-drupal-selector="drupal-settings-json">'
        '{"easa_datasets": {"data": [["Other"]]}}</script>',
    ],
)
def test_archive_format_errors(page: str) -> None:
    with pytest.raises(SourceFormatError):
        parse_archive(page)


def test_split_archive_types_keeps_raw_text() -> None:
    holders, raw, types = split_archive_types("CFM INTERNATIONAL\nCFM56-7B/CFM56-7B20/2")
    assert holders == ["CFM INTERNATIONAL"]
    assert raw == "CFM56-7B/CFM56-7B20/2"
    assert types[0].type_name == "CFM56-7B"
    assert split_archive_types("SONACA AIRCRAFT S.A.") == (["SONACA AIRCRAFT S.A."], "", [])


def test_parse_us_date_empty() -> None:
    assert parse_us_date("  ") == (None, False)


def test_interim_rows(interim_page: str) -> None:
    records, periods = parse_interim(interim_page)
    assert len(records) == 67
    assert [p.period for p in periods] == [22, 21, 20, 19, 18]
    assert periods[0].csv_url == "https://www.easa.europa.eu/en/downloads/144341/en"
    rows = by_number(records)
    assert "2026-0213" in rows
    assert rows["CF-2026-54"].issuer == "CF"
    assert rows["US-2026-21-12"].issuer == "US"
    assert rows["BR-2026-10-01"].issuer == "BR"


def test_interim_new_layout_holder_paragraph(interim_page: str) -> None:
    ad = by_number(parse_interim(interim_page)[0])["26-133"]
    assert ad.pub_class == "PAD"
    assert ad.holders == ["AIRBUS S.A.S."]
    assert [(t.type_name, t.models[:2]) for t in ad.types] == [
        ("A310", ["A310-203", "A310-203C"]),
        ("EASA STC 10013960", []),
    ]
    assert ad.effective_date is None


def test_interim_old_layout_nested_holders(interim_page: str) -> None:
    ad = by_number(parse_interim(interim_page)[0])["2026-0198"]
    assert ad.holders == ["AIRBUS HELICOPTERS", "MECAER AVIATION GROUP SpA"]
    assert [(t.holder, t.type_name) for t in ad.types] == [
        ("AIRBUS HELICOPTERS", "H160"),
        ("MECAER AVIATION GROUP SpA", "EASA STC 10080809"),
    ]
    slash = by_number(parse_interim(interim_page)[0])["2026-0211"]
    assert slash.types[0].type_name == "SA 330 / AS 332 / EC 225"


def test_interim_pad_subject_drops_comment_email(interim_page: str) -> None:
    pad = by_number(parse_interim(interim_page)[0])["26-129"]
    assert pad.pub_class == "PAD"
    assert pad.subject.startswith("Proposed AD: Flight Controls")
    assert "[at]" not in pad.subject
    assert "send comment" not in pad.subject


def test_interim_sib_class_and_document_urls(interim_page: str) -> None:
    rows = by_number(parse_interim(interim_page)[0])
    assert rows["SAIB-2026-17"].pub_class == "SIB"
    urls = [d.url for d in rows["2026-0203"].documents]
    assert urls == [
        "https://www.easa.europa.eu/downloads/144349/en",
        "https://www.easa.europa.eu/en/downloads/144350/en",
    ]
    assert [d.kind for d in rows["2026-0203"].documents] == ["pdf", "zip"]


def test_holder_cell_empty_paragraph_ignored() -> None:
    td = html.fragment_fromstring("<td><p>HOLDER</p><p> </p><ul><li>T1<ul><li>M1</li></ul></li></ul></td>")
    holders, types = parse_holder_cell(td)
    assert holders == ["HOLDER"]
    assert types[0].models == ["M1"]


@pytest.mark.parametrize(
    "page",
    [
        "<html><body><p>no table</p></body></html>",
        '<table class="table ad-list"><thead><tr><th>Number</th></tr></thead><tbody></tbody></table>',
    ],
)
def test_interim_format_errors(page: str) -> None:
    with pytest.raises(SourceFormatError):
        parse_interim(page)


def test_interim_short_row_is_format_error(interim_page: str) -> None:
    broken = interim_page.replace("<td>2026-0212</td>", "", 1).replace(
        '<td><span class="flag flag-eu flag--small" role="img" aria-label="European Commission">'
        "</span>EU</td>",
        "",
        1,
    )
    with pytest.raises(SourceFormatError):
        parse_interim(broken)


def test_csv_text(csv_text: str) -> None:
    records = parse_csv_text(csv_text)
    assert len(records) == 20
    first = records[0]
    assert (first.number, first.issue_date, first.holders, first.types_raw) == (
        "US-2026-20-14",
        "2026-10-21",
        ["BOEING"],
        "737",
    )
    assert first.effective_date is None


def test_csv_zip(csv_text: str, zip_csv: Callable[[str], bytes]) -> None:
    assert len(parse_csv_zip(zip_csv("\ufeff" + csv_text))) == 20


def test_csv_errors() -> None:
    with pytest.raises(SourceFormatError):
        parse_csv_text('"a";"b"\n')
    with pytest.raises(SourceFormatError):
        parse_csv_zip(_zip_without_csv())


def _zip_without_csv() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "x")
    return buf.getvalue()


def test_record_json_roundtrip(interim_page: str) -> None:
    record = parse_interim(interim_page)[0][0]
    assert json.loads(json.dumps(record.as_json()))["number"] == record.number
