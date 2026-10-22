"""Command line tools.

    python -m app.cli scrape [--source rightmove] [--pages 2] [--no-details]
    python -m app.cli parse rightmove search path/to/page.html     # test a parser on a saved page
    python -m app.cli parse rightmove detail path/to/page.html
    python -m app.cli probe rightmove                               # fetch page 1 + one detail, print what parsed
    python -m app.cli rescore
    python -m app.cli digest --to you@example.com [--send]           # preview (or send) a digest
    python -m app.cli demo                                           # load fake listings to try the UI
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from .db import init_db, session_scope
from .models import Listing, Recipient
from .scrapers.base import RawListing
from .scrapers.registry import SCRAPERS, get_scraper
from .store import load_profile, seed_from_config


def _print_raw(r: RawListing) -> None:
    d = asdict(r)
    d["card_text"] = d["card_text"][:300]
    d["description"] = (d["description"] or "")[:300]
    d["photos"] = d["photos"][:3] + ([f"... {len(r.photos)} total"] if len(r.photos) > 3 else [])
    d.pop("raw", None)
    print(json.dumps({k: v for k, v in d.items() if v not in (None, [], "", False)}, default=str, indent=2))


def cmd_scrape(a) -> None:
    from .pipeline import run_all, run_source
    if a.source:
        runs = [run_source(a.source, max_pages=a.pages, fetch_details=not a.no_details)]
    else:
        runs = run_all()
    for r in runs:
        print(f"{r.source}: ok={r.ok} requests={r.pages} found={r.found} new={r.new} updated={r.updated}")
        if r.error:
            print(r.error)


def cmd_parse(a) -> None:
    scraper = get_scraper(a.source)
    html = Path(a.file).read_text(encoding="utf-8", errors="replace")
    if a.kind == "search":
        items = scraper.parse_search(html, scraper.homepage)
        print(f"{len(items)} listings parsed")
        for r in items[: a.limit]:
            _print_raw(r)
    else:
        base = RawListing(source=a.source, source_id="test", url=scraper.homepage)
        _print_raw(scraper.parse_detail(html, base))


def cmd_probe(a) -> None:
    from .config import get_settings
    get_settings().dump_html = True
    scraper = get_scraper(a.source)
    with session_scope() as s:
        profile = load_profile(s)
    loc = profile.locations[0]
    url = scraper.search_url(profile, loc, 0)
    print("GET", url)
    html = scraper.fetcher.get(url, browser=scraper.uses_browser)
    items = scraper.parse_search(html, url)
    print(f"{len(items)} listings on page 1 (raw pages saved under data/dumps/{a.source}/)")
    for r in items[:3]:
        _print_raw(r)
    if items:
        print("\nDETAIL", items[0].url)
        _print_raw(scraper.fetch_detail(items[0]))
    scraper.fetcher.close()


def cmd_rescore(a) -> None:
    from .pipeline import rescore_all
    with session_scope() as s:
        print(f"re-scored {rescore_all(s)} listings")


def cmd_digest(a) -> None:
    from .emailer import send_digest
    with session_scope() as s:
        profile = load_profile(s)
        r = Recipient(name="Preview", email=a.to, last_digest_at=None)
        if a.send:
            s.add(r)
            s.flush()
        res = send_digest(s, r, profile, dry_run=not a.send, force=True)
        if a.send:
            s.delete(r)
        if res:
            subject, html = res
            out = Path("data/digest_preview.html")
            out.write_text(html)
            print(f"Subject: {subject}\nHTML written to {out}")


AREAS = [("Jesmond", "NE2", 54.9886, -1.6036), ("Heaton", "NE6", 54.9786, -1.5745), ("Sandyford", "NE2", 54.9835, -1.6017),
         ("Spital Tongues", "NE2", 54.9819, -1.6280), ("Fenham", "NE4", 54.9790, -1.6563), ("Shieldfield", "NE2", 54.9755, -1.6040),
         ("Gosforth", "NE3", 55.0062, -1.6147), ("Byker", "NE6", 54.9739, -1.5737)]
STREETS = ["Tavistock Road", "Simonside Terrace", "Mayfair Road", "Warwick Street", "Cheltenham Terrace", "Hotspur Street",
           "Grosvenor Road", "Stratford Grove", "Wingrove Road", "Rosebery Crescent", "Holly Avenue", "Sackville Road"]
PHOTO_SEEDS = ["house", "kitchen", "bedroom", "livingroom", "bathroom", "garden"]


def cmd_demo(a) -> None:
    """Fake listings so the UI can be tried without scraping."""
    from .pipeline import dedupe, enrich_and_score, upsert
    rnd = random.Random(42)
    with session_scope() as s:
        profile = load_profile(s)
        for i in range(a.n):
            area, oc, lat, lng = rnd.choice(AREAS)
            beds = rnd.choice([profile.group_size] * 4 + [profile.group_size - 1, profile.group_size + 1])
            baths = rnd.choice([1, 1, 2, 2, 2, 3])
            pppw = rnd.randrange(95, 190, 5)
            bills = rnd.random() < 0.4
            avail = date(2027, 7, 1) + timedelta(days=rnd.choice([-30, 0, 0, 0, 14, 30, 60, 75]))
            src = rnd.choice(list(SCRAPERS))
            street = rnd.choice(STREETS)
            r = RawListing(
                source=src, source_id=f"demo{i}", url=f"https://example.com/demo/{i}",
                title=f"{beds} bed terraced house", address=f"{street}, {area}, Newcastle upon Tyne {oc}",
                area=area, lat=lat + rnd.uniform(-0.006, 0.006), lng=lng + rnd.uniform(-0.008, 0.008),
                bedrooms=beds, bathrooms=baths, furnished="furnished", available_from=avail if rnd.random() > 0.1 else None,
                available_now=False, price_amount=pppw, price_period="pw", price_per_person=True,
                bills_included=bills if rnd.random() > 0.2 else None,
                description=f"Spacious {beds} bedroom student house in {area}. £{pppw} pppw{' including bills' if bills else ''}. "
                            f"{baths} bathrooms, modern kitchen, close to Newcastle University. Available for 2027/28.",
                key_features=["Fully furnished", f"{baths} bathrooms", "Close to uni", "Gas central heating"],
                photos=[f"https://picsum.photos/seed/{p}{i}/800/500" for p in rnd.sample(PHOTO_SEEDS, rnd.randint(0, 6))],
                agent_name=rnd.choice(["Demo Lettings", "Tyne Student Homes", "Jesmond Estates"]),
            )
            listing, _ = upsert(s, r, profile)
            enrich_and_score(s, listing, profile)
        dedupe(s)
        n = s.scalar(select(Listing).where(Listing.passes_filters.is_(True)).with_only_columns(Listing.id).limit(1))
    print(f"Inserted {a.n} demo listings (photos are random placeholders). Passing example id: {n}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scrape"); p.add_argument("--source", choices=list(SCRAPERS)); p.add_argument("--pages", type=int)
    p.add_argument("--no-details", action="store_true"); p.set_defaults(fn=cmd_scrape)
    p = sub.add_parser("parse"); p.add_argument("source", choices=list(SCRAPERS)); p.add_argument("kind", choices=["search", "detail"])
    p.add_argument("file"); p.add_argument("--limit", type=int, default=5); p.set_defaults(fn=cmd_parse)
    p = sub.add_parser("probe"); p.add_argument("source", choices=list(SCRAPERS)); p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("rescore"); p.set_defaults(fn=cmd_rescore)
    p = sub.add_parser("digest"); p.add_argument("--to", required=True); p.add_argument("--send", action="store_true"); p.set_defaults(fn=cmd_digest)
    p = sub.add_parser("demo"); p.add_argument("-n", type=int, default=60); p.set_defaults(fn=cmd_demo)
    a = ap.parse_args(argv)
    init_db()
    with session_scope() as s:
        seed_from_config(s)
    a.fn(a)


if __name__ == "__main__":
    main(sys.argv[1:])
