PRAGMA journal_mode = WAL;

-- One row per number per source, as last seen. Diffs run within one source.
CREATE TABLE IF NOT EXISTS source_record (
    number TEXT NOT NULL,
    source TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    data TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    present INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (number, source)
);

-- Merged view that the API serves. Rebuilt from source_record after each ingest.
CREATE TABLE IF NOT EXISTS publication (
    number TEXT PRIMARY KEY,
    base_number TEXT NOT NULL,
    revision INTEGER NOT NULL,
    class TEXT NOT NULL,
    issuer TEXT NOT NULL,
    emergency INTEGER NOT NULL,
    issue_date TEXT,
    issue_date_uncertain INTEGER NOT NULL,
    effective_date TEXT,
    subject TEXT NOT NULL,
    holders TEXT NOT NULL,
    types_raw TEXT NOT NULL,
    status TEXT NOT NULL,
    status_source TEXT,
    superseded_by_raw TEXT,
    superseded_by TEXT,
    primary_source TEXT NOT NULL,
    sources TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS publication_issue_date ON publication (issue_date);
CREATE INDEX IF NOT EXISTS publication_base ON publication (base_number);
CREATE INDEX IF NOT EXISTS publication_superseded_by ON publication (superseded_by);

CREATE TABLE IF NOT EXISTS publication_type (
    number TEXT NOT NULL,
    holder TEXT NOT NULL,
    type_name TEXT NOT NULL,
    type_key TEXT NOT NULL,
    models TEXT NOT NULL,
    model_keys TEXT NOT NULL,
    confidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS publication_type_number ON publication_type (number);
CREATE INDEX IF NOT EXISTS publication_type_key ON publication_type (type_key);

-- Links to official EASA documents. The files themselves are never stored or served.
CREATE TABLE IF NOT EXISTS document (
    url TEXT NOT NULL,
    number TEXT NOT NULL,
    source TEXT NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    final_url TEXT,
    sha256 TEXT,
    size INTEGER,
    fetched_at TEXT,
    text_extracted INTEGER NOT NULL DEFAULT 0,
    fetch_error TEXT,
    PRIMARY KEY (url, number)
);
CREATE INDEX IF NOT EXISTS document_number ON document (number);

-- Short factual fields read from the header of an EASA AD PDF.
CREATE TABLE IF NOT EXISTS extract (
    number TEXT PRIMARY KEY,
    document_url TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    pdf_issue_date TEXT,
    pdf_effective_date TEXT,
    supersedure_text TEXT,
    supersedes TEXT NOT NULL,
    revision_text TEXT,
    revises TEXT NOT NULL DEFAULT '[]',
    foreign_ad TEXT,
    applicability_header TEXT,
    extracted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL,
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS change_event_number ON change_event (number);
CREATE INDEX IF NOT EXISTS change_event_observed ON change_event (observed_at);

CREATE TABLE IF NOT EXISTS sync_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER NOT NULL DEFAULT 0,
    origin TEXT,
    rows INTEGER,
    new_rows INTEGER,
    changed_rows INTEGER,
    message TEXT
);

CREATE TABLE IF NOT EXISTS csv_period (
    period INTEGER PRIMARY KEY,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    csv_url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    ingested_at TEXT
);

-- Numbers in a biweekly CSV that the interim table did not have.
CREATE TABLE IF NOT EXISTS reconciliation_gap (
    period INTEGER NOT NULL,
    number TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (period, number)
);
