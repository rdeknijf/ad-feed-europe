"""Settings and the fixed texts that every surface of the service shows."""

import os
from dataclasses import dataclass
from pathlib import Path

# Product name: change it here only.
APP_TITLE = "AD Feed Europe"

SITE_URL = "https://adfeed.deknijf.com"
REPO_URL = "https://github.com/rdeknijf/ad-feed-europe"

# Identifies the service to EASA, with the terms page as the contact point.
USER_AGENT = f"ADFeedEurope/1.0 (+{SITE_URL}/terms)"

EASA_BASE = "https://www.easa.europa.eu"
INTERIM_URL = (
    f"{EASA_BASE}/en/domains/aircraft-products/airworthiness-directives-ad/"
    "safety-publications-tool-interim-solution"
)
ARCHIVE_URL = (
    f"{EASA_BASE}/en/domains/aircraft-products/airworthiness-directives-ad/archive-airworthiness-directives"
)
EASA_TOOL_URL = "https://ad.easa.europa.eu/"
EASA_FAQ_URL = f"{EASA_BASE}/en/the-agency/faqs/safety-publications-sp-tool"
EASA_COPYRIGHT_URL = f"{EASA_BASE}/en/copyright-disclaimer"

SOURCE_NAME = "EASA, Safety Publications interim page, AD archive page and biweekly CSV lists"

UNOFFICIAL_NOTICE = "Unofficial mirror of data that EASA publishes. Not affiliated with or endorsed by EASA."
DISCLAIMER = (
    "Not a source for airworthiness compliance. Always confirm against the official EASA publication."
)

SOURCES = ("interim", "archive", "csv", "pdfs")


@dataclass(frozen=True)
class Settings:
    database_path: Path
    private_dir: Path
    stale_after_hours: float
    request_delay_seconds: float


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("EASA_AD_DATA_DIR", "data"))
    return Settings(
        database_path=Path(os.environ.get("EASA_AD_DATABASE", str(data_dir / "easa_ads.db"))),
        private_dir=Path(os.environ.get("EASA_AD_PRIVATE_DIR", str(data_dir / "private"))),
        stale_after_hours=float(os.environ.get("EASA_AD_STALE_HOURS", "6")),
        request_delay_seconds=float(os.environ.get("EASA_AD_REQUEST_DELAY", "2.5")),
    )
