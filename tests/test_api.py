import sqlite3
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from easa_ad_api import queries
from easa_ad_api.config import APP_TITLE, DISCLAIMER, UNOFFICIAL_NOTICE

ATOM = "{http://www.w3.org/2005/Atom}"


def child(node: ET.Element, tag: str) -> ET.Element:
    found = node.find(f"{ATOM}{tag}")
    assert found is not None, tag
    return found


def text(node: ET.Element, tag: str) -> str:
    return child(node, tag).text or ""


JSON_ROUTES = [
    "/v1/ads",
    "/v1/ads?type=A350",
    "/v1/ads/2026-0199",
    "/v1/ads/DOES-NOT-EXIST",
    "/v1/ads?limit=0",
    "/v1/types",
    "/v1/types/A350/ads",
    "/v1/changes",
    "/v1/status",
]


@pytest.mark.parametrize("route", JSON_ROUTES)
def test_every_json_response_carries_the_guards(client: TestClient, route: str) -> None:
    body = client.get(route).json()
    meta = body["meta"]
    assert meta["unofficial"] is True
    assert meta["disclaimer"] == DISCLAIMER
    assert meta["notice"] == UNOFFICIAL_NOTICE
    assert meta["terms"] == "/terms"
    assert "as_of" in meta
    assert "stale" in meta
    assert "stale_warning" in meta


@pytest.mark.parametrize("route", ["/", "/terms", "/v1/changes.atom", "/docs", "/openapi.json"])
def test_every_page_carries_the_disclaimer(client: TestClient, route: str) -> None:
    response = client.get(route)
    assert response.status_code == 200
    text = response.text
    if route == "/docs":
        text = client.get("/openapi.json").text
    assert DISCLAIMER in text


def test_ad_detail(client: TestClient) -> None:
    data = client.get("/v1/ads/2026-0199").json()["data"]
    assert data["status"] == "active"
    assert data["official_documents"][0]["url"].startswith("https://www.easa.europa.eu/")
    assert data["official_source_page"].startswith("https://www.easa.europa.eu/")
    sup = data["supersedure"]
    assert [s["number"] for s in sup["supersedes"]] == ["2026-0086"]
    assert sup["superseded_by"] is None
    assert data["pdf_extract"]["applicability_header"].startswith('SZD-54-2 "Perkoz"')
    assert data["types"][0]["parse_confidence"] == "high"


def test_superseded_chain(client: TestClient) -> None:
    data = client.get("/v1/ads/2026-0086").json()["data"]
    assert data["status"] == "superseded"
    assert data["superseded_by"] == "2026-0199"
    assert data["supersedure"]["latest_in_chain"] == "2026-0199"
    assert data["supersedure"]["chain_newer"][0]["number"] == "2026-0199"
    newer = client.get("/v1/ads/2026-0203").json()["data"]["supersedure"]
    assert "2025-0206R1" in [s["number"] for s in newer["chain_older"]]


def test_lowercase_number_lookup(client: TestClient) -> None:
    assert client.get("/v1/ads/cf-2026-48").status_code == 200


def test_not_found_has_envelope(client: TestClient) -> None:
    response = client.get("/v1/ads/9999-0000")
    assert response.status_code == 404
    assert "No publication" in response.json()["error"]


def test_validation_error_has_envelope(client: TestClient) -> None:
    response = client.get("/v1/ads?match=fuzzy")
    assert response.status_code == 422
    assert response.json()["meta"]["disclaimer"] == DISCLAIMER


@pytest.mark.parametrize(
    ("query", "expected", "absent"),
    [
        ("type=A350", "2026-0210", None),
        ("type=a350-941", "2026-0210", None),
        ("type=TRENT%201000", "2026-0203", None),
        ("type=CFM56-7B20&match=contains", "26-130", None),
        ("type=A35&match=contains", "2026-0210", None),
        ("type=A35", None, "2026-0210"),
        ("holder=boeing&issuer=FAA", "US-2026-20-14", "2026-0210"),
        ("class=EAD", "2026-0191-E", "2026-0199"),
        ("status=superseded", "2026-0086", "2026-0199"),
        ("q=Torsion", "2026-0199", None),
        ("issued_from=2026-11-04&issued_to=2026-11-04", "2026-0212", "2026-0199"),
        ("effective_from=2026-11-18&effective_to=2026-11-18", "2026-0212", "2026-0199"),
        ("q=100%25", None, "2026-0199"),
    ],
)
def test_search_filters(client: TestClient, query: str, expected: str | None, absent: str | None) -> None:
    body = client.get(f"/v1/ads?{query}&limit=500").json()
    numbers = {d["number"] for d in body["data"]}
    assert body["total"] == len(numbers)
    if expected:
        assert expected in numbers
    if absent:
        assert absent not in numbers


def test_type_ads_and_types(client: TestClient) -> None:
    body = client.get("/v1/types/A350/ads").json()
    assert body["type"] == "A350"
    assert all("A350" in d["types_text"].upper() for d in body["data"])
    types = client.get("/v1/types?q=A350").json()
    assert types["data"][0]["type"] == "A350"
    assert types["data"][0]["parse_confidence"] == "high"
    assert client.get("/v1/types?holder=boeing&limit=5").json()["total"] >= 1


def test_changes_feed_excludes_baseline_and_pages(client: TestClient) -> None:
    body = client.get("/v1/changes?limit=5").json()
    assert len(body["data"]) == 5
    assert all(c["change_type"] != "imported" for c in body["data"])
    cursor = body["next_after_id"]
    nxt = client.get(f"/v1/changes?limit=5&after_id={cursor}").json()["data"]
    assert nxt[0]["id"] > cursor
    everything = client.get("/v1/changes?include_imported=true&limit=500").json()["data"]
    assert any(c["change_type"] == "imported" for c in everything)
    assert client.get("/v1/changes?since=2099-01-01T00:00:00%2B00:00").json()["data"] == []
    status = client.get("/v1/changes?change_type=status_changed&limit=500").json()["data"]
    assert status
    assert {c["change_type"] for c in status} == {"status_changed"}


def test_atom_feed_per_type_and_emergency(client: TestClient, loaded: sqlite3.Connection) -> None:
    response = client.get("/v1/changes.atom?type=A350")
    assert response.headers["content-type"].startswith("application/atom+xml")
    feed = ET.fromstring(response.content)
    entries = feed.findall(f"{ATOM}entry")
    assert entries
    assert DISCLAIMER in text(feed, "subtitle")
    for entry in entries:
        assert DISCLAIMER in text(entry, "content")
        assert (
            child(entry, "link[@rel='alternate']").get("href", "").startswith("https://www.easa.europa.eu/")
        )

    loaded.execute(
        "INSERT INTO change_event (number, change_type, source, observed_at) VALUES"
        " ('2026-0191-E', 'status_changed', 'archive', '2026-11-05T00:00:00+00:00')"
    )
    loaded.commit()
    emergency = ET.fromstring(client.get("/v1/changes.atom?class=EAD").content)
    entry = child(emergency, "entry")
    assert text(entry, "title").startswith("[EMERGENCY AD]")
    child(entry, "category[@term='emergency']")


def test_status_endpoint(client: TestClient) -> None:
    data = client.get("/v1/status").json()["data"]
    assert set(data["sources"]) == {"interim", "archive", "csv", "pdfs"}
    assert data["sources"]["interim"]["last_successful_sync"]
    assert data["counts"]["publications"] > 100
    assert data["csv_reconciliation"]["csv_periods_loaded"] == [22]
    assert data["csv_reconciliation"]["alert"] is False


def test_stale_flag_everywhere(client: TestClient, loaded: sqlite3.Connection) -> None:
    old = (datetime.now(UTC) - timedelta(hours=10)).replace(microsecond=0).isoformat()
    loaded.execute("UPDATE sync_run SET started_at=? WHERE source='interim'", (old,))
    loaded.commit()
    meta = client.get("/v1/ads").json()["meta"]
    assert meta["stale"] is True
    assert "Stale data" in meta["stale_warning"]
    assert client.get("/v1/status").json()["data"]["stale"] is True
    page = client.get("/").text
    assert 'class="stale"' in page
    assert "Stale data" in page
    assert "Stale data" in text(ET.fromstring(client.get("/v1/changes.atom").content), "subtitle")


def test_fresh_page_has_no_stale_banner(client: TestClient, loaded: sqlite3.Connection) -> None:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    loaded.execute("UPDATE sync_run SET started_at=? WHERE source='interim'", (now,))
    loaded.commit()
    assert 'class="stale"' not in client.get("/").text


def test_never_synced_is_stale(conn: sqlite3.Connection) -> None:
    fresh = queries.freshness(conn, 6)
    assert fresh.stale is True
    assert fresh.as_of is None


def test_landing_page_content(client: TestClient) -> None:
    page = client.get("/").text
    assert APP_TITLE in page
    assert "Unofficial mirror" in page
    assert "Not a compliance source" in page
    assert "/terms" in page
    assert "/docs" in page
    assert "https://www.easa.europa.eu/" in page
    assert "2026-0210" in page
    for dash in ("—", "–"):
        prose = page.split("<tbody>")[0] + page.split("</tbody>")[-1]
        assert dash not in prose
    assert "price" not in page.lower()
    assert "api key" not in page.lower()


def test_landing_page_custom_and_unknown_type(client: TestClient) -> None:
    assert "TBM 700" in client.get("/?type=TBM%20700").text
    assert "No publication lists" in client.get("/?type=NOPE-0").text


def test_terms_page(client: TestClient) -> None:
    page = client.get("/terms").text
    for phrase in (
        "free",
        "unofficial",
        "without any warranty",
        "incomplete, wrong or late",
        "stop at any time",
    ):
        assert phrase in page
    assert "email alerts" in page
    assert "https://github.com/rdeknijf/ad-feed-europe" in page
    assert "corrected or removed" in page


def test_no_route_serves_pdf_files(client: TestClient) -> None:
    paths = {route.path for route in client.app.routes}
    assert not any("pdf" in p.lower() and p != "/v1/changes.atom" for p in paths)
    assert client.get("/static/site.css").status_code == 200
