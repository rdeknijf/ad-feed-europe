# AD Feed Europe

[![ci](https://github.com/rdeknijf/ad-feed-europe/actions/workflows/ci.yml/badge.svg)](https://github.com/rdeknijf/ad-feed-europe/actions/workflows/ci.yml)

A free, unofficial JSON API and Atom feeds over the airworthiness directives (ADs) that EASA publishes. While EASA's Safety Publications Tool is unavailable, EASA lists new ADs in an HTML table on an interim page. This service turns that table, the frozen AD archive and EASA's biweekly CSV lists into one searchable API with change feeds per aircraft type.

Live at **<https://adfeed.deknijf.com>**. API docs at [`/docs`](https://adfeed.deknijf.com/docs).

> Unofficial mirror of data that EASA publishes. Not affiliated with or endorsed by EASA. Not a source for airworthiness compliance. Always confirm against the official EASA publication. See the [terms](https://adfeed.deknijf.com/terms).

## Quick start

```bash
curl "https://adfeed.deknijf.com/v1/ads/2026-0199"
```

```json
{
  "meta": {
    "service": "AD Feed Europe",
    "unofficial": true,
    "disclaimer": "Not a source for airworthiness compliance. Always confirm against the official EASA publication.",
    "as_of": "2026-11-06T11:05:46+00:00",
    "stale": false,
    "terms": "/terms"
  },
  "data": {
    "number": "2026-0199",
    "class": "AD",
    "issuer": "EASA",
    "issue_date": "2026-10-20",
    "effective_date": "2026-11-03",
    "subject": "Flight Controls – Airbrake Control Torsion Tube – Inspections",
    "holders": ["ALLSTAR PZL GLIDER"],
    "types_text": "SZD-54 Perkoz: SZD-54-2 Perkoz",
    "status": "active",
    "official_documents": [
      {
        "url": "https://www.easa.europa.eu/download/AD-tmp-storage/EASA_AD_2026-0199_1.pdf",
        "label": "PDF (307 kb)",
        "kind": "pdf",
        "listed_on": "interim"
      }
    ],
    "supersedure": {
      "supersedes": [{ "number": "2026-0086", "status": "superseded", "issue_date": "2026-04-30" }],
      "latest_in_chain": "2026-0199"
    },
    "pdf_extract": {
      "supersedure": "This AD supersedes EASA AD 2026-0086 dated 30 April 2026.",
      "applicability_header": "SZD-54-2 \"Perkoz\" sailplanes, all serial numbers (s/n)."
    }
  }
}
```

The response is trimmed here. Every field is in the [OpenAPI docs](https://adfeed.deknijf.com/docs).

Follow all changes for one type in a feed reader:

```text
https://adfeed.deknijf.com/v1/changes.atom?type=A350
```

## Endpoints

| Endpoint | Description |
| --- | --- |
| `GET /v1/ads` | Search and filter: `type`, `holder`, `issuer`, `class`, `status`, `q`, issue and effective date ranges |
| `GET /v1/ads/{number}` | One AD with official documents, supersedure chain, status and PDF header fields |
| `GET /v1/types` | Type designations with AD counts |
| `GET /v1/types/{type}/ads` | All ADs listed for one type or model |
| `GET /v1/changes` | Change events (new, status, dates, subject, documents), paged by id |
| `GET /v1/changes.atom` | Atom feed of changes, filtered by type, holder, class or issuer |
| `GET /v1/status` | Last successful sync per source, stale flag, counts |

You do not need an API key or an account. All responses are JSON. Every response carries the unofficial notice, the disclaimer and the time of the last successful sync in `meta`. When that sync is more than 6 hours old, `meta.stale` is `true`.

## Data source

The data comes from three public EASA pages: the [Safety Publications interim page](https://www.easa.europa.eu/en/domains/aircraft-products/airworthiness-directives-ad/safety-publications-tool-interim-solution), the [AD archive](https://www.easa.europa.eu/en/domains/aircraft-products/airworthiness-directives-ad/archive-airworthiness-directives) and the biweekly CSV lists linked from the interim page. It adds selected header fields from EASA's AD PDFs. EASA's [copyright notice](https://www.easa.europa.eu/en/copyright-disclaimer) applies to EASA's content.

- Every record links to the EASA page that lists it, and to the official EASA document where EASA lists one.
- The service links to EASA's PDF files. It does not store or serve copies. The worker downloads each new EASA AD PDF into a temporary file, extracts the text and deletes the file. It fetches a PDF again only when its document URL changes or an extraction failed. It keeps the extracted text private. From the header it publishes only the dates, the supersedure and revision references, the foreign AD reference and at most 300 characters of applicability text.
- The service gives no applicability verdicts and does no serial number or part number matching.
- There is no warranty. The data can be incomplete, wrong or late. To report an error or to ask for a removal, [open an issue](https://github.com/rdeknijf/ad-feed-europe/issues).

## How it works

A worker polls the interim page every 30 minutes, the archive weekly and each new biweekly CSV once, with a fixed User-Agent and at least 2.5 seconds between requests. Parsers turn each source into records, and SQLite keeps one row per number per source with a content hash. After each sync the service merges the sources into one publication per number (interim first, then archive, then CSV), derives the status from subject signals and PDF headers, and writes a change event when the status, effective date, issue date or subject changes, or when a document is added. FastAPI serves the JSON API, the Atom feeds and two server-rendered pages.

## Local development

Needs [uv](https://docs.astral.sh/uv/). `pdftotext` (poppler) is needed only for `fetch-pdfs`.

```bash
uv sync
uv run prek install                # git hooks: ruff, ty, pytest
uv run pytest                      # network is blocked in tests

# Load data and serve on http://127.0.0.1:8602/
uv run easa-ad-api ingest-archive  # one live request to EASA
uv run easa-ad-api ingest-interim  # one live request to EASA
uv run easa-ad-api serve --port 8602
```

Each `ingest-*` command also takes `--from-file` to load a saved copy of a page without a request. Please keep live requests to EASA few.

`docker-compose.yml` is the live setup (Traefik labels and an external `proxy` network). Adapt it for a local container run.

Stack: Python 3.14, FastAPI, httpx, lxml, Jinja2, SQLite, click, uv, ruff, ty, pytest.

## Licence

The code is under the [MIT licence](LICENSE). EASA's content stays under EASA's [copyright notice](https://www.easa.europa.eu/en/copyright-disclaimer).
