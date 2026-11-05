# anatomy.md

> Auto-maintained by OpenWolf. Last scanned: 2026-11-05T20:06:00.623Z
> Files: 77 tracked | Anatomy hits: 0 | Misses: 0

> Project structure index. Auto-maintained by OpenWolf hooks and daemon.
> Run `openwolf scan` to generate, or wait for the first Claude Code session.
> Status: Pending initial scan

## ./

- `.dockerignore` — Docker ignore rules (~14 tok)
- `.gitignore` — Git ignore rules (~19 tok)
- `CLAUDE.md` — OpenWolf (~244 tok)
- `config.example.yaml` — Copied into the database on FIRST start only. After that, edit everything in the web UI (~481 tok)
- `docker-compose.claude.yml` — Lets the AI overview / Q&A use the Claude Code CLI with your Claude subscription (no API key). (~241 tok)
- `docker-compose.playwright.yml` — Docker Compose: 0 services (~32 tok)
- `docker-compose.yml` — Docker Compose services (~118 tok)
- `Dockerfile` — Docker container definition (~152 tok)
- `Dockerfile.playwright` — Variant with headless Chromium for Cloudflare-protected sites (Zoopla). ~1.5 GB image. (~171 tok)
- `PLAN.md` — House finder: plan and working notes (~7482 tok)
- `README.md` — Project documentation (~647 tok)
- `requirements-dev.txt` (~12 tok)
- `requirements.txt` — Python dependencies (~72 tok)

## app/

- `__init__.py` (~0 tok)
- `access_token.py` — Verifies the Cloudflare Access login token. The plain email header can be faked by anyone who reaches the port directly. (~281 tok)
- `access.py` — Manage who can log in: the email allow-list of the Cloudflare Access policy in front of the site. (~1643 tok)
- `ai.py` — AI overview and Q&A for a listing. (~4658 tok)
- `api.py` — JSON API used by the web UI. (~6117 tok)
- `bills.py` — Estimate household bills when a listing doesn't include them. (~577 tok)
- `cli.py` — Command line tools. (~3205 tok)
- `config.py` — Environment-level settings (secrets, paths). User-editable search settings live in profile.py. (~1136 tok)
- `db.py` — Base: get_engine, reset_engine, init_db, session_scope + 1 more (~860 tok)
- `emailer.py` — Email digests, instant 'great place' alerts and reminders. (~2693 tok)
- `geo.py` — Geocoding (postcodes.io, free, no key) and commute-time estimates. (~2336 tok)
- `main.py` — API: GET (7 endpoints) (~1198 tok)
- `models.py` — SQLAlchemy: Setting (settings) (~2884 tok)
- `photos.py` — Fetch listing photos with an on-disk cache (used by the /img proxy and the AI overview). (~531 tok)
- `pipeline.py` — Scrape -> normalise -> enrich -> score -> events. (~7124 tok)
- `profile.py` — The search profile: everything the user can tweak from the Settings page. (~2059 tok)
- `scheduler.py` — Background jobs: periodic scraping, digest emails, instant alerts. (~1067 tok)
- `scoring.py` — Hard filters + a 0-100 score for each listing against the profile. (~1882 tok)
- `store.py` — Load/save the Profile (stored as JSON in the settings table). (~394 tok)
- `textparse.py` — Free-text parsing helpers. (~5639 tok)

## app/scrapers/

- `__init__.py` (~0 tok)
- `afs.py` — Accommodation for Students (accommodationforstudents.com). (~2042 tok)
- `base.py` — View: get (~2724 tok)
- `extract.py` — Generic extraction helpers shared by all site scrapers. (~3007 tok)
- `generic.py` — Shared logic for sites where we read listing cards as text + detail label/value pairs. (~1685 tok)
- `hive.py` — Hive Estates (hiveestates.com): Newcastle letting agent (Jesmond/Heaton/Fenham, students and professionals). (~1487 tok)
- `openrent.py` — OpenRent (openrent.co.uk): landlord-direct lets, no agency fees. (~1586 tok)
- `registry.py` — get_scraper (~195 tok)
- `rightmove.py` — Rightmove (rightmove.co.uk). Biggest portal; most Newcastle student agents list here. (~3636 tok)
- `spareroom.py` — SpareRoom (spareroom.co.uk). Mostly single rooms; we keep only whole-property ads. (~1607 tok)
- `sturents.py` — StuRents (sturents.com): student houses, prices per person per week. (~3090 tok)
- `unihomes.py` — UniHomes (unihomes.co.uk): all-bills-included student houses, priced per person per week. (~1578 tok)
- `zoopla.py` — Zoopla (zoopla.co.uk). Behind Cloudflare: usually needs HF_PLAYWRIGHT_ENABLED=true. (~798 tok)

## app/static/

- `ai_log.js` — AI log (admins only): who used the AI, on which place, and roughly what it cost. (~892 tok)
- `app.css` — Styles: 101 rules, 31 vars, 6 media queries, 1 animations (~9315 tok)
- `common.js` — Shared helpers for all pages (no build step, plain ES2020). (~2324 tok)
- `listing.js` — Listing detail page. (~5608 tok)
- `listings.js` — Listings page: filters, sort, cards / table / map views. (~6540 tok)
- `manifest.webmanifest` (~128 tok)
- `settings.js` — Settings page: edits the profile JSON and the email recipients. (~4302 tok)
- `status.js` — Status page: scrape runs, emails, manual triggers. (~632 tok)

## app/static/vendor/leaflet/

- `leaflet.css` — Styles: 143 rules (~4231 tok)
- `leaflet.js` — l: N, a, h + 25 more (~42158 tok)

## app/templates/

- `ai_log.html` (~142 tok)
- `base.html` — {% block title %}House finder{% endblock %} (~562 tok)
- `email_digest.html` — Declares html (~891 tok)
- `email_digest.txt` (~269 tok)
- `index.html` (~958 tok)
- `listing.html` (~83 tok)
- `settings.html` (~1489 tok)
- `status.html` (~292 tok)

## tests/

- `__init__.py` (~0 tok)
- `access_jwt.py` — Sign Cloudflare Access style tokens for tests. (~170 tok)
- `conftest.py` — access_login, session (~267 tok)
- `test_access.py` — Tests: list_add_remove, only_admins_can_edit, not_configured, no_matching_app (~1030 tok)
- `test_ai.py` — Tests: context_has_group_and_listing_facts, ai_disabled_without_key, overview_and_questions_are_saved, photos_dropped_when_api_rejects_them + 6 more (~2688 tok)
- `test_api.py` — Tests: api_roundtrip (~489 tok)
- `test_emailer.py` — Tests: digest_contents_and_rendering, reminders_for_great_unactioned, send_digest_dry_run_and_failure_logged, digest_due_windows (~804 tok)
- `test_pipeline.py` — Tests: bills_calibration, normalise_prefers_text_pppw, normalise_academic_year_hint, upsert_events_and_price_drop + 16 more (~4351 tok)
- `test_scrapers.py` — Parser tests on small synthetic pages that mirror the live structures seen on 2026-10-22. (~8095 tok)
- `test_textparse.py` — Snippets below are verbatim (or near-verbatim) text seen on real listings in Oct 2026. (~1933 tok)
- `test_viewer.py` — Tests: faked_email_header_is_not_believed, verified_token_is_believed_and_case_ignored, non_admin_token_cannot_edit, bad_tokens_cannot_edit + 4 more (~647 tok)

## tests/fixtures/

- `hive_detail.html` (~576 tok)
- `hive_search.html` (~889 tok)
