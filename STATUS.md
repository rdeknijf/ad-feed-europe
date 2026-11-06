# STATUS

Attention: maintain

Work items: beads epic `idea-machine-u03` in the idea-machine project (this repo has no beads database).

## Goal

A free, unofficial public API and Atom feeds over the EASA airworthiness directive list, with the four guards in `AGENTS.md`. Public repo: https://github.com/rdeknijf/ad-feed-europe.

## Live

- Public as "AD Feed Europe" at https://adfeed.deknijf.com since 2026-11-06. Docker compose runs `api` and `worker` on the host `buckland`, behind Traefik and Cloudflare. The Traefik rate limit is 10 requests per second on average, burst 40, per `CF-Connecting-IP` (60 parallel requests gave 46 x 200 and 14 x 429).
- Verified 2026-11-06 11:05 UTC: `/v1/status` shows a successful interim sync at 11:05:46 (71 rows), so the worker runs. 17,482 publications.
- An uptime probe on the host alerts when `/` fails twice. It does not see the stale flag: a dead worker shows only as the stale banner. The host backup covers the live database (`~/services/adfeed/data/easa_ads.db`), and the host's `~/services/update.sh` rebuilds this stack with the others.
- Public wording was fact-checked on 2026-11-06 by two independent models; five overclaims were fixed and redeployed (`da8f942`).
- Redeployed 2026-11-06 about 12:10 UTC: the worker sends the User-Agent `ADFeedEurope/1.0 (+https://adfeed.deknijf.com/terms)` (`USER_AGENT` in `src/easa_ad_api/config.py`), and `/terms` has the "Corrections and removal" section.
- Deploy a new version: `git ls-files | rsync -a --files-from=- ./ buckland:services/adfeed/` from a checkout with the LFS fonts present, then `ssh buckland 'cd ~/services/adfeed && docker compose up -d --build'`.

## Repo state (2026-11-06)

- Checks pass: `uv run prek run --all-files`, `uv run pytest` (141 tests, 95% coverage), `uv audit --locked`. The same steps pass in a fresh clone without LFS content, which is what `.github/workflows/ci.yml` runs.
- Test fixtures hold no full EASA documents: the PDF text fixtures are first-page header excerpts (129 KB to 11 KB), the interim fixture holds only its two tables (355 KB to 73 KB). The parsers give identical output on the old and the new fixtures.
- History scan (no gitleaks or trufflehog installed, grep of `git log -p --all`): no credentials. Older commits still hold the full PDF text and interim page fixtures, with third-party support email addresses and EASA page tokens. A squash to one fresh root commit removes them.
- No LICENSE file yet.

## Decisions

- Free: no keys, tiers, billing or prices. No webhooks; `/v1/changes` (paged) and Atom feeds instead.
- No fleet check, no applicability verdicts, no serial or part number matching. This can be reversed; it would need a careful parse of the applicability text and adds compliance risk.
- Transport Canada (CF-) rows are link-only; FAA rows are metadata plus a link; PDF headers only for EASA ADs, EADs and ADRs.
- Takedown path: an issue on the GitHub repo. On a reasonable request from EASA, records or the whole service go offline.
- `PRODUCT.md` is inferred from the build brief; Rutger has not confirmed it.

## Blockers

- A question to EASA (reuse terms, polling load, the future of their tool) is not sent; it waits for Rutger's go.

## Resume path

1. Read `AGENTS.md` (guards, load discipline, commands) and `README.md`.
2. `uv sync`, then `uv run pytest`.
3. `uv run easa-ad-api serve --port 8602` and open `http://127.0.0.1:8602/`. The page shows "Stale" once the last interim sync is 6 hours old; refresh with one `uv run easa-ad-api ingest-interim` (one live request).
