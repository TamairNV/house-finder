# House finder: plan and working notes

> Written for future Claude sessions working on this repo (Tamair said they won't read it).
> Keep it current: when you change behaviour, verify a site, or answer an open question, update this file.
> Last updated: 2026-10-22 (first build session, cloud sandbox + read-only recon on TNV-server).

## 1. What Tamair asked for (2026-10-22, paraphrased closely)

- Find a **house share for next year with friends**. Tenancy should start **around July** (2027).
- A **web hub** where they enter parameters: locations, preferences, and **places people go a lot** (uni/work),
  **one or more per person** ("everyone's main uni place").
- Settings: **min bathrooms per person**; **number of people as a hard limit**; **price with a buffer**,
  **including bills**, with a **bill estimate when bills aren't included**; **move-in date** (many places are
  "move in now", which is useless for a July start).
- **As much info as possible** per place, **pictures**, a nice **sortable/filterable** UI instead of clunky sites.
- Use **multiple sites**.
- Runs on their **mini PC server**, **emails on set intervals** with new places, and **reminds them of really good places**.
- **Configure who gets which kind of emails**.
- A git repo for it. Do as much as possible without their Mac/server/questions.

## 2. Context we inferred (confirm with Tamair, see section 12)

- Tamair is a **Computer Science student at Newcastle University** (GitHub profile README; module repo `csc2035`).
  So the default search area is **Newcastle upon Tyne** and the default key place is Newcastle University (NE1 7RU).
  Northumbria University City Campus is right next door; Coach Lane campus is ~4 km east (Benton).
- Group size unknown: default `group_size: 4`. Budget unknown: default £150 pppw all-in + £15 buffer.
- **Timing is urgent.** Newcastle student houses for Sept/July 2027 go live from ~November 2026; most groups sign
  Nov-Jan; "the best stock in Jesmond is gone by January" (studentbeans, Aug 2026). Recon on 2026-10-22 already
  showed most 4-bed Rightmove results with July 2027 availability. Get the scraper running ASAP.
- Newcastle student areas and typical rents (pppw, studentbeans Aug 2026): Jesmond £80-180, Heaton £55-165,
  Sandyford £70-180, Shieldfield £120-170 (bills mixed), Ouseburn £120-195, Fenham £80-170, Gosforth £110-230,
  city centre £150-250 (usually bills inc). Heaton's "8th/9th/10th Avenue" area is popular. Newcastle has an
  Article 4 HMO direction and city-wide additional HMO licensing (licensed HMOs are a good sign).
- Almost all private student tenancies are 12 months; many start 1 July (or late June/early July) which suits Tamair.

## 3. Status

### Done (in this repo)
- FastAPI app + SQLite (SQLAlchemy 2), APScheduler background jobs, vanilla-JS UI (no build step), Leaflet maps (vendored).
- Profile/settings model (`app/profile.py`) editable in the UI (Settings page) and seeded from `config.yaml` on first run.
- Scrapers for Rightmove, OpenRent, UniHomes, Accommodation for Students (AFS), StuRents, SpareRoom, Zoopla (disabled).
- Free-text parsing (`app/textparse.py`): pppw/pcm prices, "inc bills" variants, bills packages, availability dates,
  bedrooms/bathrooms, postcodes, academic years. 44 unit tests built from real listing snippets.
- Normalisation, enrichment (postcodes.io geocoding, commute estimates, bills estimate), hard filters + 0-100 score,
  cross-site de-duplication, events (new / price drop / let agreed / removed / great), email digests + instant alerts +
  reminders, per-recipient email rules, photo cache proxy (`/img`), CLI tools, Dockerfile + compose.
- 55 tests passing (`pytest -q`), UI smoke-tested with demo data (`python -m app.cli demo`).

### Verified vs not
- Page **structures** for all sites were captured from TNV-server on 2026-10-22 (curl, saved in `~/house-finder-probe/*.html`
  on the server, summary in section 5). Scrapers were rewritten against that report and tests mirror it.
- **Not yet run against the live sites end-to-end.** The cloud sandbox can't reach listing sites. First job of the next
  session on the server/Mac: run `python -m app.cli parse ...` on the saved probe pages, then `python -m app.cli probe <site>`
  for each site, fix mismatches, then turn real HTML into test fixtures (see 9.1).

### Repo / hosting state
- Code lives in this git repo. The cloud session can't create GitHub repos, so it was handed over as files/bundle; Tamair
  was asked to create private `TamairNV/house-finder` and add the server's SSH key (`~/.ssh/id_ed25519.pub`,
  fingerprint SHA256:FfyZ6XOzHBTdhpEhPJeeNiJNdG1WQMbE892DHN88k0I) as a deploy key. Check whether that happened.

## 4. Architecture

```
scheduler (APScheduler, in-process)
  ├─ scrape job every N h (skips quiet hours) ──> pipeline.run_all()
  │     for each enabled source: scraper.search() -> RawListing cards
  │        -> candidate? (bedrooms fit/unknown) -> scraper.fetch_detail() (once, then every 3 days)
  │        -> upsert() [Event new/price_drop/let_agreed] -> enrich_and_score() [geocode, bills, commutes, evaluate]
  │     -> mark unseen > 36 h as removed -> dedupe() -> send_instant_alerts()
  └─ email job every 10 min -> run_due_digests() (per-recipient schedule)
FastAPI: / (cards/table/map), /listing/{id}, /settings, /status, /api/*, /img (photo cache)
SQLite: data/house_finder.db (WAL). Photos cached in data/photos/.
```

| File | Role |
|---|---|
| `app/config.py` | env settings (`HF_*`): SMTP, base URL, auth, delays, Playwright, ORS key |
| `app/profile.py` | **Profile** (all user-tweakable search settings), people/places, bills assumptions, weights |
| `app/store.py` | load/save profile JSON in `settings` table; seed from `config.yaml` (+ recipients) on first start |
| `app/models.py` | Listing, Event, Recipient, ScrapeRun, EmailLog, GeoCache, Setting |
| `app/textparse.py` | free-text parsing (prices, bills, dates, rooms, postcodes) |
| `app/scrapers/base.py` | RawListing, polite Fetcher (per-host delay+jitter, retries, challenge detection, HTML dumps, optional Playwright) |
| `app/scrapers/extract.py` | generic helpers: __NEXT_DATA__, JSON-LD, JS assignments/arrays, card finder, label/value pairs, lat/lng |
| `app/scrapers/generic.py` | CardScraper base (card text + detail label/value parsing) |
| `app/scrapers/<site>.py` | per-site scrapers |
| `app/pipeline.py` | normalise, upsert, enrich, score, dedupe, run_source/run_all |
| `app/bills.py` | bill estimator |
| `app/geo.py` | postcodes.io geocoding (cached), haversine, commute estimate, optional openrouteservice |
| `app/scoring.py` | hard filters + score |
| `app/emailer.py` | digest building/rendering/sending, instant great alerts, schedule check |
| `app/scheduler.py` | background jobs, manual trigger |
| `app/api.py`, `app/main.py` | JSON API, pages, basic auth middleware, photo proxy |
| `app/cli.py` | `scrape`, `parse`, `probe`, `rescore`, `digest`, `demo` |

Design choices: single container, SQLite, no JS build, everything configurable from the UI. Per-person-per-week (pppw)
is the canonical price unit because that's how UK student lets are quoted.

## 5. Data sources (recon from TNV-server 2026-10-22)

General: use a desktop Chrome UA, en-GB, ≥4 s between requests per host (+jitter), at most a few hundred requests
per run. Personal, non-commercial use. Respect robots.txt paths below. If a site starts challenging, back off/disable.

### Rightmove ✅ structured, highest value
- Search `https://www.rightmove.co.uk/property-to-rent/find.html?locationIdentifier=REGION%5E984&minBedrooms=4&maxBedrooms=4&maxPrice=…&index=0&sortType=6&includeLetAgreed=false`
  (REGION^984 = Newcastle Upon Tyne). `__NEXT_DATA__` → `props.pageProps.searchResults.properties` (24-25/page),
  `searchResults.pagination.next` = next index. Fields: id, bedrooms, bathrooms (0 = unknown), summary, displayAddress,
  location{latitude,longitude}, images[{srcUrl (crop), url (relative full-size, prefix https://media.rightmove.co.uk/)}],
  letAvailableDate (ISO), price{amount,frequency,displayPrices}, customer{branchDisplayName,contactTelephone},
  keyFeatures[{description}], students, displayStatus, firstVisibleDate, addedOrReduced, propertyTypeFullDescription.
- Detail `/properties/{id}`: `window.__PAGE_MODEL = {"data": "<json string>", "encoding": "on"}`; data is a flat
  devalue-style array where ints are indexes (`rightmove.decode_flat`). `propertyData`: lettings{letAvailableDate
  "DD/MM/YYYY"|"Now", deposit, minimumTermInMonths, letType, furnishType}, prices{primaryPrice, secondaryPrice},
  bedrooms, bathrooms, images[{url}], floorplans, keyFeatures[str], text.description (HTML), location, address{outcode,
  incode}, livingCosts{councilTaxBand,…}, nearestStations, status, features.
- Gotchas: some agents enter **pppw as the weekly price** (e.g. 4-bed "£135 pw"): treated as per-person if weekly < £300
  and 3+ beds. Free text "£145 PPPW" usually present. Bills only in free text; "Compulsory Additional £24PPPW Bills
  Package" seen. Structured bathroom count can disagree with text.
- robots allows `/property-to-rent/find.html` and `/properties/`; `/api/*` disallowed. **T&Cs prohibit scraping.**

### OpenRent ✅ arrays
- Search `/properties-to-rent/newcastle-upon-tyne?term=Newcastle%20upon%20Tyne` returns **every** area listing (~250) as
  parallel JS arrays: PROPERTYIDS, prices (pcm), bedrooms, bathrooms, isshared (room in share → skip), isstudio,
  islivelistBool, students, bills, availableFrom (days from today, negative = now), minimumTenancy (months), furnished,
  unfurnished, PROPERTYLISTLATITUDES/LONGITUDES, dateFirstListedMs. URL bedroom filters are ignored server-side.
  Some arrays are 1 shorter than PROPERTYIDS → only use arrays with matching length (done).
- Detail: `https://www.openrent.co.uk/{id}` redirects to canonical. `<table>` rows `td.fw-medium` label + value; booleans
  as svg `text-success` (tick) / `text-danger` (cross); `data-lat`/`data-lng`; `#descriptionText`; "N tenants max."
- robots fine. Landlord-direct, no agency fees. Mostly non-student lets; July availability rarer.

### UniHomes ✅ JSON in Vue prop (all bills included)
- `/student-accommodation/newcastle?page=N` (also `/{n}-bedroom-student-houses` pages, 4-bed URL untested).
  Listings in `:initial-properties="JSON.parse('…')"` (HTML-entity + JS-string escaped). Fields: id, bedroomCount,
  bathroomCount, price (pppw string), availableFrom ("Available from 10th July 2027"), latitude/longitude (strings),
  postcode, path, propertyName, street, images[{fileURLTablet}], mainImage, shortDescription, type, advertType.
  Sibling attrs: `:initial-properties-has-more-pages`, `:initial-property-count` (~795 Newcastle).
- Detail page not probed; search JSON has nearly everything, so `fetch_details: false` by default.

### Accommodation for Students (AFS) ✅ __NEXT_DATA__
- `/newcastle/student-houses/{n}-bedroom-student-houses?page=N` (robots disallows `/search-results*`).
  `props.pageProps.viewModel.properties.groups[*].results[*].property`: id, url, coordinates{lat,lon}, address{…,postcode},
  terms{rentPpw{value}, billsIncluded "none"/…}, numberOfBathrooms, occupancy{total}=bedrooms, contracts[{startDate}],
  images[{url}], accommodationProvider{name}, academicYearLabel, isSoldOut. `viewModel.pageCount`.
- Detail `/property/{id}-slug`: `viewModel.propertyDetails`: description (HTML entities), availability{availableNow,
  availableFromDate "7 July 2027"}, bills{whichBillsAreIncluded, billsPackageQuote{totalBillsPerWeek}}, price{rentPerWeekFrom,
  deposit}, amenities[{name}] (often contains "Compulsory Additional £28PPPW Bills Package"), coordinates, rooms[].
- Many listings are agent feeds (Walton Robinson etc.) duplicated on Rightmove → dedupe matters.

### StuRents ⚠️ partial
- robots: `Disallow: /` but allows `/student-accommodation`, `/house`, `/listings`, `/sitemap`, … ; `/ajax/*` disallowed.
- City landing `/student-accommodation/newcastle-upon-tyne` server-renders only **8 cards** (`a[data-house]` JSON with
  beds, price pppw, from, bills_included, lat/lng, photo_urls, description, landlord, rental_type).
  Full results (~422) load client-side from an unknown XHR (`/s/...` page, `data-search-token`). Current approach:
  landing cards + `https://sturents.com/sitemap/houses/{city-slug}.xml` (verified 2026-10-22: the index lists
  `houses/newcastle-upon-tyne.xml` and `houses-deactivated/...`; slug taken from the landing cards' URLs).
  The profile's `/student-accommodation/newcastle` page has 12 cards with **`data-house="{}"`** (empty): beds/price/date
  come from card text, photo from `data-photo`, street + landlord from a JSON-LD `Residence` per card (description
  ends "- {id}"). TODO: find the XHR in devtools on the Mac, check it isn't under /ajax (robots).
- Detail: JSON-LD `@type House` (numberOfBedrooms, numberOfBathroomsTotal, address.postalCode, geo) +
  `p.test-availability-{bills-included,date-from,date-to,rent}`; photos `public.sturents.com/photos/N.jpg`.

### SpareRoom ⚠️ low yield
- `/flatshare/newcastle_upon_tyne[/pageN]` only (robots disallows most filter params). Cards `li.listing-result[data-listing-*]`.
  Mostly single rooms. Classified on `span.listing-card__room` only (descriptions of single rooms often mention "5 bed
  house"): "3 bed flat" = whole property (total rent); "6 doubles" = several rooms in one house, kept with per-room
  (per-person) rent; "Double room"/"Single room" skipped. Detail per-room rents in `.feature--price_room_only`
  (`<dt>£750 pcm</dt><dd>double (En suite)</dd>`). Detail `dl.feature-list` dt/dd;
  `location: {latitude: "..", longitude: ".."}`. No bathroom field. Probably not worth many pages.

### Zoopla ❌ blocked
- Cloudflare "Just a moment" 403 for curl. robots also disallows `/search/`, `/property/` etc. but `/to-rent/property/{area}/`
  and `/to-rent/details/{id}/` are allowed. Scraper exists but disabled; would need `Dockerfile.playwright`
  (headless Chromium) and may still be challenged. Most Newcastle agents cross-list on Rightmove, so low priority.

### Not covered (ideas)
- Local agents' own sites (Walton Robinson, Mike Rogerson, Bradley Hall, Robinsons, Ryder Lets, Kingston, Groves, Yetholm,
  MyHouse, Sanderson Young…): many feed Rightmove/AFS already. Add only if a big agent is missing.
- OnTheMarket (`onthemarket.com`, Next.js) - plausible next source. Facebook groups / Unipol: no.
- Newcastle University's own "NU Student Homes"/accredited landlord list if it exists - check.

## 6. Normalisation rules (pipeline.normalise + textparse.guess_rent)

- Canonical: `rent_pppw` (per person per week), `rent_total_pcm` (whole house), `total_pppw` = rent + bills
  (0 if included, stated package if found, else estimate).
- Whole-property prices are divided by **group size** (not bedrooms) when bedrooms ∈ allowed range.
- Free-text per-person mentions ("£145 PPPW", "£170 per person per week") beat structured whole-house prices.
  "£139pppw (£166pppw inc bills)" → rent 139, bills package 27. Deposits/holding fees ignored.
- `pcm ↔ pw`: × 52/12. Student sites (AFS, UniHomes, StuRents) are pppw.
- Dates: explicit date > "available now" > academic year hint ("2027/28" → assume 1 July 2027, flagged).
- Sanity: a "whole property" structured price under £50 pppw for 3+ people is treated as a room rent
  (`price_source = structured-room`), e.g. Rightmove "5 bedroom house share" at £450 pcm.
- Bathrooms: structured value; 0 = unknown; else text ("2 bathrooms", "4 en-suites").
- Dedupe: same bedrooms + (same street key & outcode, or exact coords within 60 m) + pppw within 8%. Canonical = most photos.
  Status/favourite propagate. Duplicates show as "Also on".

## 7. Bills estimate (`app/bills.py`, all numbers editable in Settings → Bill assumptions)

Calibrated so a 5-person house uses ~4,100 kWh electricity + ~17,000 kWh gas/yr (Fused Bills Newcastle "high usage"
5-bed ≈ £202/month at 2026 prices). Unit rates from the Ofgem cap forecast for winter 2026/27 (pocketwise, Mar 2026):
electricity ~25p/kWh + ~60p/day, gas ~6.5p/kWh + ~32p/day. EPC multiplier A 0.65 … G 1.45. Water £42/month (Northumbrian
Water average, Fused) + £3/person. Broadband £32. TV licence off by default (£174.50 if on; check tvlicensing.co.uk).
Council tax £0 (all-student households are exempt; one non-student makes the house liable!). +10% contingency.
Result for 4 people, EPC D: ≈ £290/month house ≈ **£17 pppw**. Agent bills packages in Newcastle are commonly
£20-30 pppw, which is higher (they include margin + no usage cap).
TODO: update unit rates when Ofgem announces the Jan-Mar 2027 cap (late Nov 2026) and again in Feb for Apr-Jun.

## 8. Filters and score (`app/scoring.py`)

Hard filters (fail → hidden unless "Only places that fit my rules" is unticked; reasons shown on cards):
listing active; bedrooms ∈ [group_size, group_size + extra_bedrooms_allowed]; bathrooms/people ≥ min (unknown = warning
by default); total_pppw ≤ budget + buffer; available within [earliest, latest] (available-now fails explicitly);
furnished preference; excluded keywords/areas; distance from first person's first place ≤ max_distance_km; per-place
hard commute limits; "professionals only" (if enabled).
Score 0-100 = weighted mean of: price (1.0 at ≤75% of budget → 0.6 at budget → 0 at budget+buffer), commute (per place
1.0 up to half the max minutes → 0.3 at max → 0 at 1.5× max; 70% weighted average + 30% worst person, for fairness),
bathrooms (ratio ≥ 0.5 → 1.0), move-in closeness to target, bills included, photos count, preferred area.
"Great" = passes + score ≥ threshold (80 default) → Event('great') → instant email + reminders.

Commute: straight-line × detour ÷ speed (walk 5 km/h ×1.2, cycle 15 ×1.25, transit 16 ×1.35 + 8 min, drive 22 ×1.4 + 5).
Optional `HF_ORS_API_KEY` (openrouteservice free tier) gives real walk/cycle/drive times (`geo.ors_minutes`, not yet wired
to run by default: `enrich_and_score(use_routing_api=True)` - add a setting and cache results per listing).
Transit (Metro/bus) would need Google Routes API (`HF_GOOGLE_MAPS_API_KEY`, not implemented) - only worth it if someone
in the group commutes to Coach Lane / work.

## 9. Next steps (priority order for the next session)

1. ~~Get the code onto the server~~ Done 2026-10-22 (see session log). Remaining from that step: real-page fixtures (9.1).
   Original steps, for reference:
   - `python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest -q`
   - Run parsers on the saved probe pages: `python -m app.cli parse rightmove search ~/house-finder-probe/rm_search.html` etc.
     (file names: rm_search, rm_detail, openrent_search, openrent_detail, unihomes_p1/p2, afs_search, afs_detail,
     sturents_search, sturents_detail, spareroom_search, spareroom_detail).
   - Live check: `HF_DUMP_HTML=true python -m app.cli probe rightmove` (and each other site).
   - Fix mismatches. **Copy trimmed real pages into `tests/fixtures/` and add tests** (9.1).
2. **Deploy** (section 10). Create `.env` (SMTP!) and `config.yaml` with the real group/places/budget.
3. Ask Tamair the open questions (section 12), update the profile.
4. StuRents: discover the search XHR (devtools on the Mac) or confirm the sitemap layout.
5. Wire openrouteservice commute times (cache per listing+place; only for passing listings).
6. Nice-to-haves: per-listing "send to group" button; WhatsApp/Telegram/ntfy push alongside email; viewing calendar;
   compare view (2-4 houses side by side); vote per person (each housemate thumbs up/down); OnTheMarket scraper;
   price-per-bathroom metric; "days on market"; floorplan room sizes; HMO licence lookup (Newcastle public register).

### 9.1 Parser fixtures
Keep real HTML small: strip `<style>`, unrelated scripts and most cards. Name `tests/fixtures/<site>_<kind>.html`
and assert on concrete values. Never commit personal data beyond what's on the public listing.

## 10. Deployment on TNV-server

Facts (recon 2026-10-22): Ubuntu 24.04, Docker 29 + compose v5 (user `tnv` in docker group), Nginx Proxy Manager
container on 80/81/8443, cloudflared tunnels, Tailscale active (100.126.112.62), LAN 192.168.1.199. Port **8095** free.
Python 3.12 on host. No Chromium.

```bash
cd ~ && git clone git@github.com:TamairNV/house-finder.git && cd house-finder
cp .env.example .env && nano .env            # SMTP (Gmail app password), HF_BASE_URL, HF_UI_USERNAME/PASSWORD
cp config.example.yaml config.yaml && nano config.yaml   # group size, budget, people + places, recipients
mkdir -p data && docker compose up -d --build
docker compose logs -f                        # first scrape starts ~2 min after boot
```
- Access: `http://100.126.112.62:8095` over Tailscale (simplest, private). For friends without Tailscale either share
  via Tailscale or add an NPM proxy host / cloudflared hostname **with basic auth on** (HF_UI_USERNAME/PASSWORD).
- Backups: `data/house_finder.db` (SQLite; `sqlite3 data/house_finder.db ".backup data/backup.db"`). Photos are a cache.
- Update: `git pull && docker compose up -d --build`.
- Zoopla/Playwright variant: `docker compose -f docker-compose.yml -f docker-compose.playwright.yml up -d --build`.

## 11. Legal / etiquette

- Rightmove's footer: "Rightmove prohibits the scraping of its content". Zoopla similar. robots.txt allows the paths we use.
  This tool is for one person's household search, low volume (a few hundred requests every few hours), no
  redistribution. Tamair accepts the risk; tell them plainly (done in the first summary). Keep delays polite, honour
  429/Retry-After, stop if blocked, never bypass logins/captchas, never resell or publish the data.
- Emails contain listing links + thumbnails only to the configured recipients.
- Don't store anything about other people beyond the public listing and agent contact details.

## 12. Tamair's answers (2026-10-22) — applied to profile defaults + config.example.yaml

1. Newcastle only. Tamair (Comp Sci) → Urban Sciences Building NE4 5TG; some of the group → Medical School NE2 4HH.
   Heaton is cheaper so it's a compromise. Heaton, Jesmond and City Centre are all in the mix.
2. Group of 5 (subject to change).
3. No hard commute caps, just not far away (max_distance_km 5).
4. Budget ~£130 pppw incl bills (subject to change). Buffer set to £10.
5. 0.4 bathrooms per person → 2 bathrooms for 5 is fine (Tamair, 2026-10-22; 0.5 meant 3 bathrooms and left only 3 listings passing).
6. July best, June ok → target 2027-07-01, window 2027-06-01 to 2027-08-01.
7. Must be furnished.
8. Only Tamair gets emails for now: daily digest when there's something new (empty digests are skipped). Instant alerts off.
9. Sender: Tamair's own address or a new one on their domains. Recommendation: a dedicated sender on
   tamairnv.co.uk via their mail provider's SMTP (or Gmail + app password to start).
10. Public URL https://house.tamairnv.co.uk: dashboard-managed Cloudflare tunnel run as its own container
    `house-finder-tunnel` (`--network host`, token in `~/house-finder-tunnel/tunnel.env`, mode 600; ingress
    `http://localhost:8095` — must be http, the app has no TLS). Guarded by a Cloudflare Access app (verified: every
    path 302s to isotopicstudios.cloudflareaccess.com), so HF_UI_USERNAME/PASSWORD are left empty. Other tunnels on the
    box (systemd cloudflared + 4 containers) are separate; don't touch them.
11. Used Rightmove last year; no site preference. Zoopla stays off.
12. All students (council tax £0).

## 12b. AI overview + Q&A (2026-10-22)

- `app/ai.py`: per-listing overview (Markdown, saved to `Listing.ai_overview`) and Q&A (`Listing.ai_chat`), shown
  on the listing page. Endpoints: `POST /api/listings/{id}/ai/overview`, `POST .../ai/ask`, `DELETE .../ai/chat`.
- Backends (`HF_AI_PROVIDER`, default auto): `api` = Claude API with `HF_ANTHROPIC_API_KEY`; `claude_cli` = Claude
  Code headless (`claude -p`, stream-json, `--tools ""`, photos inlined as base64) on Tamair's Claude subscription.
  Tamair asked: use the subscription if possible; if it needs a paid key, keep the feature hidden (auto = hidden
  when neither is available).
- On TNV-server only the CLI binary is mounted (`docker-compose.claude.yml`); auth is a `claude setup-token` token
  in `.env` (CLAUDE_CODE_OAUTH_TOKEN), so the ~/.claude login folder never enters the internet-facing container.
- Only ever user-triggered (button / question), never automatic per scrape, to keep subscription usage sane.

## 12c. Login allow-list panel (2026-10-22)

- Settings → "Who can log in" edits the Cloudflare Access Allow policy for HF_BASE_URL's host via the CF API
  (`app/access.py`, `/api/access`). Needs HF_CF_API_TOKEN (Access: Apps and Policies: Edit). Only HF_ADMIN_EMAILS can
  edit, identified by the Cf-Access-Authenticated-User-Email header (so LAN access without Cloudflare can't edit).

## 13. Session log

- 2026-10-22 (cloud): research, recon on server via Remote Control (read-only), full build, 77 tests, UI checked with
  demo data. Couldn't create GitHub repo; asked Tamair to create `TamairNV/house-finder` + deploy key.
- 2026-10-22 (TNV-server): cloned, 77 tests passed; parsers checked against saved and live pages. Fixed: StuRents crash on
  `data-house="[]"`, city-page `data-house="{}"` cards, sitemap walk (was reading Aberdeen), deposit; OpenRent detail
  photos (og:image is an older listing id; use `a.lightbox_item`); SpareRoom room-vs-house classification, per-room
  rents, "Available now"; AFS £0.00 bills quote / 0 deposit; UniHomes detail photos (175 → 16); per-room price sanity
  rule; `/api/status` runs were never saved (expunge before commit). 88 tests. Deployed via docker compose on :8095.
  First scrape: rightmove 127, unihomes 108, afs 60, sturents 12, spareroom 7, openrent 4 (no errors). Only 3 pass the
  filters; 22 more failed only on 2 bathrooms < 0.5 per person; Tamair said 2 for 5 is fine → 0.4, 20 pass.
