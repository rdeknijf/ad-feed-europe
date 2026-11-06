# AD Feed Europe

A free, unofficial JSON API and Atom feeds over the airworthiness directives (ADs) and related safety publications that EASA publishes on its website. It reads three public EASA sources (the Safety Publications interim page, the AD archive page, and the CSV list that EASA publishes every two weeks), merges them into SQLite, and serves `/v1/*` endpoints, Atom feeds, a landing page and a terms page with FastAPI.

The product name is "AD Feed Europe". It lives in one constant: `APP_TITLE` in `src/easa_ad_api/config.py`.

Endpoints and local use: `README.md`. Current state, decisions and blockers: `STATUS.md`. Product context for UI work: `PRODUCT.md`.

## Commands

```zsh
uv sync
uv run prek install
uv run ruff check --fix .
uv run ruff format .
uv run ty check .
uv run pytest                      # coverage report included
uv run prek run --all-files

uv run easa-ad-api serve --port 8602          # local server, http://127.0.0.1:8602/
uv run easa-ad-api ingest-archive --from-file data/raw/archive.html   # offline re-ingest
uv run easa-ad-api ingest-interim --from-file data/raw/interim.html
uv run easa-ad-api ingest-csv --period 22 --from-file <zip>
uv run easa-ad-api fetch-pdfs --dry-run       # list PDF candidates, no requests
uv run easa-ad-api reparse-pdfs               # re-parse stored PDF text, no requests
```

The same `ingest-*` and `fetch-pdfs` commands without `--from-file` make live requests to EASA. Read "Load discipline" first.

Environment: `EASA_AD_DATA_DIR` (default `data`), `EASA_AD_DATABASE`, `EASA_AD_PRIVATE_DIR` (default `data/private`), `EASA_AD_STALE_HOURS` (default 6), `EASA_AD_REQUEST_DELAY` (default 2.5 seconds).

`pdftotext` (poppler) must be installed for `fetch-pdfs`.

## Rules

### Guards (mandatory, never remove)

1. Label the service everywhere as an UNOFFICIAL MIRROR of data that EASA publishes, not affiliated with EASA, and never a compliance source. The strings are `UNOFFICIAL_NOTICE` and `DISCLAIMER` in `config.py`. Every JSON response carries them in `meta`; the landing page, terms page, OpenAPI description and every Atom feed and entry show them. Tests in `tests/test_api.py` enforce this.
2. Every record links to the official EASA document and the official EASA page. Never serve, publicly cache or mirror EASA PDF files. Keep only short factual fields: number, dates, supersedure references, the applicability header line (max 300 characters). `fetch-pdfs` writes a PDF only to a temporary folder and keeps only the extracted text, under `data/private/` (not served).
3. Show the last successful sync per source. When the last successful interim sync is older than `EASA_AD_STALE_HOURS`, every response sets `stale: true` with a warning, the landing page shows a banner, and `/v1/status` says so.
4. Keep the no-warranty terms page at `/terms`, with links to EASA's own free email alerts and tool.

### Scope

- Free. No API keys, tiers, billing or prices. No webhooks.
- No fleet check, no applicability verdicts, no serial number or part number matching (a reversible decision, see `STATUS.md`).
- Transport Canada (CF-) rows are link-only. FAA rows are metadata plus a link. Fetch PDF headers only for EASA ADs, EADs and ADRs.

### Load discipline (live requests to EASA)

- Use `Fetcher` in `ingest.py`: it sends the project User-Agent (`USER_AGENT` in `config.py`: service name and terms page URL, no email) and keeps at least `EASA_AD_REQUEST_DELAY` seconds between requests.
- On HTTP 403, HTTP 429 or any sign of blocking, `Fetcher` raises `BlockedError`. Stop and report. Do not retry with other headers.
- Per manual session: at most one archive fetch, one interim fetch, one CSV zip and 30 PDFs. Prefer `--from-file` on the saved copies in `data/raw/` for development.
- The schedule (compose `worker`): interim every 30 minutes, archive weekly, CSV every two weeks.

### Deploy

- No deploy, DNS, publication, public repo or push without Rutger's explicit go.
- Never start the `worker` service or any scheduled polling without that go.
- Do not contact EASA (for example ads@easa.europa.eu) without Rutger's go.

### Stack

- Python 3.14, uv with `uv_build`, src layout, FastAPI, httpx, lxml, Jinja2, SQLite, click.
- ruff, ty, pytest with pytest-cov and pytest-socket (network blocked in tests), prek hooks in `.pre-commit-config.yaml`. Never use `--no-verify`.
- Modern typing, pathlib, absolute imports. No `__init__.py` in `tests/`. Parameterize repeated test cases.
- Latest stable dependencies, no upper-bound pins.
- Binary files (fonts, images) go through Git LFS (`.gitattributes`). Commit no EASA PDF files.

## Layout

| Path | Purpose |
| --- | --- |
| `src/easa_ad_api/config.py` | Title, URLs, guard strings, settings |
| `src/easa_ad_api/parsers.py` | Archive (Drupal settings JSON), interim (`table.ad-list`), CSV zip parsers |
| `src/easa_ad_api/pdf.py` | PDF first-page header parser (`pdftotext -layout` output) |
| `src/easa_ad_api/numbers.py` | Number normalization, issuer and class, subject status signals |
| `src/easa_ad_api/store.py`, `schema.sql` | Source records, merged publications, change events, sync runs |
| `src/easa_ad_api/ingest.py` | Fetcher, ingest entry points, PDF fetch |
| `src/easa_ad_api/queries.py`, `api.py` | Query layer and FastAPI app |
| `src/easa_ad_api/templates/`, `static/` | Landing and terms pages, CSS, self-hosted fonts (OFL) |
| `tests/fixtures/` | Golden fixtures (see below) |

## Data model notes

- `source_record` holds one row per number per source with a content hash. `publication` is rebuilt after each ingest; field priority is interim, then archive, then CSV.
- Status comes from subject signals in any source (superseded, cancelled, withdrawn, not adopted; PAD gives proposed) and from PDF `Supersedure:` and `Revision:` lines and higher revision numbers.
- The first archive load writes `imported` events, which `/v1/changes` and the Atom feeds hide by default. Later loads write `new`, `status_changed`, `effective_date_changed`, `subject_changed`, `issue_date_changed` and `document_added`.
- Archive type lists are one string joined by "/", and model names can contain "/". Archive type splits are marked `parse_confidence: low`.
- 553 archive rows carry the year 1980 (flagged `issue_date_uncertain`); 203 rows have no effective date.

## Fixtures

- `tests/fixtures/interim.html`: the biweekly table and the safety publications table (67 rows) of the real interim page, fetched 2026-11-04. The page chrome is removed.
- `tests/fixtures/archive_trimmed.html`: 44 real archive rows in the original Drupal settings JSON wrapper.
- `tests/fixtures/biweekly_22_reconstructed.csv`: reconstructed from the parsed rows of period 22, because the raw zip of the first live fetch was not saved. `fetch_csv` now saves the raw zip to `data/private/raw/`; replace this fixture with the real file after the next CSV fetch.
- `tests/fixtures/pdf-text/*.txt`: header excerpts of the first page of 10 real EASA AD PDFs (`pdftotext -layout` text): only the labelled fields that `pdf.py` reads, up to the end of the applicability block. Never add full PDF text: EASA's PDFs are "All rights reserved".
