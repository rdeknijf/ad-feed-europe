# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI serves server-rendered Jinja2 pages (`/`, `/terms`) next to the JSON API. Plain HTML and CSS, no build step, no external scripts or fonts required.

## Users

People who need the list of new and changed airworthiness directives (ADs) per aircraft type while EASA's Safety Publications Tool (ad.easa.europa.eu) is unavailable: continuing-airworthiness staff (CAMO), maintenance-software developers, and owners who want a machine-readable feed or a per-type alert in a feed reader. They are professionals who already know what an AD, a CAMO and a type designation are.

## Product Purpose

A free, unofficial JSON API and per-type Atom feeds over the AD metadata that EASA publishes on its interim page, its frozen archive page and its biweekly CSV lists. It saves people from reading a hand-kept HTML table. Success: a developer understands in one screen what it is, what it is not, how fresh the data is, and how to call it.

## Positioning

The only thing it adds is structure and change detection over EASA's own published list (supersedure written back into old rows, per-type feeds). It is never a compliance source and never claims applicability to an aircraft.

## Capabilities and Constraints

- Endpoints: `/v1/ads`, `/v1/ads/{number}`, `/v1/types`, `/v1/types/{type}/ads`, `/v1/changes`, `/v1/changes.atom`, `/v1/status`, OpenAPI at `/docs`.
- Free: no keys, accounts, tiers, or prices anywhere.
- No serial-number or part-number matching, no fleet check.
- Never serves or mirrors EASA's PDF files; links to them.
- Product name "AD Feed Europe" lives in one constant, `APP_TITLE`.

## Brand Commitments

Mandatory on every surface: an "unofficial mirror, not affiliated with EASA" notice; "Not a source for airworthiness compliance. Always confirm against the official EASA publication."; last successful sync time and a stale-data banner; links to official EASA documents; a no-warranty terms page. No em dashes or en dashes in prose. No EASA logo, colours that imitate EASA's site, or anything that could pass as an official EASA page.

## Evidence on Hand

Real local data from EASA's pages (17,500 rows). No customers, testimonials, usage numbers, or endorsements exist; do not invent any.

## Product Principles

1. Honesty over polish: the limits (unofficial, maybe late, maybe wrong) are as visible as the features.
2. Freshness is shown, never implied.
3. The official source is always one click away.
4. Developer-first: show the real request and the real response.

## Accessibility & Inclusion

WCAG 2.2 AA contrast; works at 390 px wide; banners must be readable without colour alone.
