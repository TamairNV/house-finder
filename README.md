# House finder

Self-hosted house-share finder for a group of friends. It scrapes UK listing sites (Rightmove, OpenRent,
UniHomes, Accommodation for Students, StuRents, SpareRoom), works out the **real cost per person per week
including bills**, checks your group's hard rules (people, bathrooms, budget, move-in window), scores every
place on price, commute for each person, bathrooms and move-in date, and gives you a clean web UI with
photos, filters, a map and a shortlist. It emails digests of new places and nags you about the great ones.


## Quick start (Docker)

```bash
cp .env.example .env               # SMTP + base URL + optional UI password
cp config.example.yaml config.yaml # your group, budget, places (only read on first start)
docker compose up -d --build
open http://localhost:8095
```

Everything in `config.yaml` can be changed later on the **Settings** page. The first scrape starts two minutes
after boot, then every few hours (Settings → Sites). **Status** shows each run and lets you trigger one.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/python -m app.cli demo           # fake listings to play with the UI
HF_SCHEDULER_ENABLED=false .venv/bin/uvicorn app.main:app --reload
```

Useful CLI commands:

| Command | What it does |
|---|---|
| `python -m app.cli scrape [--source rightmove --pages 2]` | run scrapers now |
| `python -m app.cli probe rightmove` | fetch one search page + one listing and print what was parsed (saves HTML to `data/dumps/`) |
| `python -m app.cli parse rightmove search page.html` | test a parser on a saved page |
| `python -m app.cli digest --to me@example.com [--send]` | preview or send a digest |
| `python -m app.cli rescore` | recompute filters/scores after changing rules |

## How costs are worked out

Rent is normalised to **£ per person per week**. If bills aren't included, the agent's stated bills package is used
when the listing mentions one ("£24 pppw bills package"), otherwise an estimate from energy price-cap rates, house
size, EPC rating, water, broadband and (optionally) TV licence. All assumptions are editable in Settings.

## Notes

- Be polite: default 4 s+ between requests per site, a few hundred requests per run. Some sites' terms forbid
  scraping; this is for personal use only. See `PLAN.md` §11.
- Zoopla is behind Cloudflare and disabled by default (`Dockerfile.playwright` is an experimental headless-browser variant).
- `PLAN.md` holds the detailed design notes, site-by-site findings and the roadmap.
