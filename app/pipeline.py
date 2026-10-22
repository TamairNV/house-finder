"""Scrape -> normalise -> enrich -> score -> events.

Flow per source run:
1. walk search pages, collect RawListing cards
2. upsert each into ``listings`` (new ones get an Event('new'))
3. for candidates (bedrooms fit or unknown) fetch the detail page once, and
   again every DETAIL_REFRESH_DAYS to catch price changes / let agreed
4. enrich (geocode, bills estimate, totals, commutes) and score
5. mark listings not seen for REMOVED_AFTER_HOURS as removed
"""
from __future__ import annotations

import logging
import re
import traceback
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import geo
from .bills import estimate_bills
from .db import session_scope
from .models import Event, Listing, ScrapeRun, utcnow
from .profile import Profile
from .scoring import evaluate
from .scrapers.base import BlockedError, RawListing
from .scrapers.registry import SCRAPERS, get_scraper
from .store import load_profile
from .textparse import (WEEKS_PER_MONTH, academic_year_hint, detect_bills_included, guess_rent, is_student_let,
                        parse_available, parse_bathrooms, parse_bedrooms, parse_furnished, parse_postcode)

log = logging.getLogger(__name__)

DETAIL_REFRESH_DAYS = 3
REMOVED_AFTER_HOURS = 36
MAX_DETAILS_PER_RUN = 60


# ------------------------------------------------------------------ normalise

def normalise(raw: RawListing, profile: Profile, today: date | None = None) -> dict:
    """RawListing -> dict of Listing column values."""
    today = today or date.today()
    text = raw.text_blob
    bedrooms = raw.bedrooms if raw.bedrooms is not None else parse_bedrooms(text)
    bathrooms = raw.bathrooms if raw.bathrooms is not None else parse_bathrooms(text)
    rent = guess_rent(text, bedrooms, profile.group_size if bedrooms in profile.allowed_bedrooms else bedrooms,
                      raw.price_amount, raw.price_period, raw.price_per_person)
    bills_included = raw.bills_included if raw.bills_included is not None else rent.bills_included
    if bills_included is None:
        bills_included = detect_bills_included(text)

    available_from, available_now = raw.available_from, raw.available_now
    if available_from is None:
        available_from, available_now = parse_available(text, today)
    if available_from is None:
        ay = academic_year_hint(text)
        if ay:
            # "2027/28" lets usually start July-September; assume 1 July for scoring, flagged.
            available_from = date(ay[0], 7, 1)

    postcode, outcode = raw.postcode, None
    if postcode:
        outcode = postcode.split()[0]
    else:
        postcode, outcode = parse_postcode(" ".join(filter(None, [raw.address, raw.title])))

    return dict(
        url=raw.url, title=raw.title, address=raw.address, area=raw.area or _area_from_address(raw.address),
        postcode=postcode, outcode=outcode,
        lat=raw.lat, lng=raw.lng, geo_precision="exact" if raw.lat is not None else None,
        property_type=raw.property_type, bedrooms=bedrooms, bathrooms=bathrooms,
        furnished=raw.furnished or parse_furnished(text), epc=raw.epc,
        student_let=raw.student_let if raw.student_let is not None else is_student_let(text),
        rent_pppw=rent.pppw, rent_total_pcm=rent.total_pcm, price_source=rent.source,
        bills_included=bills_included, bills_pppw_stated=rent.bills_pppw,
        deposit=raw.deposit, min_term_months=raw.min_term_months,
        available_from=available_from, available_now=available_now,
        description=raw.description, key_features=raw.key_features or [],
        agent_name=raw.agent_name, agent_phone=raw.agent_phone,
        photos=[p for p in raw.photos if p][:40], floorplans=[p for p in raw.floorplans if p][:5],
        listed_at=raw.listed_at,
        raw={**(raw.raw or {}), "card_text": raw.card_text[:4000]},
    )


def _area_from_address(addr: str | None) -> str | None:
    """'Cheltenham Terrace, Heaton, Newcastle Upon Tyne NE6' -> 'Heaton'."""
    if not addr:
        return None
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    parts = [p for p in parts if not re.search(r"newcastle|tyne and wear|^[A-Z]{1,2}\d", p, re.I)]
    if len(parts) >= 2:
        return re.sub(r"\s+[A-Z]{1,2}\d[A-Z\d]?$", "", parts[-1]).strip() or None
    return None


# ------------------------------------------------------------------ upsert

KEEP_IF_NONE = {"lat", "lng", "geo_precision", "bathrooms", "available_from", "deposit", "description", "epc",
                "furnished", "postcode", "outcode", "agent_name", "agent_phone", "min_term_months", "area",
                "rent_pppw", "rent_total_pcm", "bills_pppw_stated", "bills_included", "title", "address"}


def upsert(session: Session, raw: RawListing, profile: Profile) -> tuple[Listing, bool]:
    vals = normalise(raw, profile)
    listing = session.scalar(select(Listing).where(Listing.source == raw.source, Listing.source_id == raw.source_id))
    now = utcnow()
    is_new = listing is None
    if is_new:
        listing = Listing(source=raw.source, source_id=raw.source_id, first_seen=now, price_history=[], **vals)
        session.add(listing)
        session.flush()
        session.add(Event(listing_id=listing.id, kind="new"))
    else:
        old_pppw = listing.rent_pppw
        for k, v in vals.items():
            if k in KEEP_IF_NONE and v is None:
                continue
            if k in ("photos", "floorplans", "key_features") and not v:
                continue
            if k == "raw":
                v = {**(listing.raw or {}), **v}
            setattr(listing, k, v)
        if old_pppw and listing.rent_pppw and abs(listing.rent_pppw - old_pppw) >= 1:
            kind = "price_drop" if listing.rent_pppw < old_pppw else "price_rise"
            session.add(Event(listing_id=listing.id, kind=kind, data={"from": old_pppw, "to": listing.rent_pppw}))
        if listing.site_status == "removed":
            session.add(Event(listing_id=listing.id, kind="back"))
    if listing.rent_pppw and (not listing.price_history or listing.price_history[-1]["pppw"] != listing.rent_pppw):
        listing.price_history = [*(listing.price_history or []), {"at": now.isoformat(timespec="minutes"), "pppw": listing.rent_pppw}]
    new_status = "let_agreed" if raw.let_agreed else "active"
    if listing.site_status != new_status and not is_new and new_status == "let_agreed":
        session.add(Event(listing_id=listing.id, kind="let_agreed"))
    listing.site_status = new_status
    listing.last_seen = now
    if raw.detail_fetched:
        listing.detail_fetched_at = now
    return listing, is_new


# ------------------------------------------------------------------ enrich + score

def enrich_and_score(session: Session, listing: Listing, profile: Profile, use_routing_api: bool = False) -> None:
    # Location: exact coords from the site, else postcode, else postcode district centroid.
    if listing.lat is None:
        if listing.postcode:
            c = geo.geocode_postcode(session, listing.postcode)
            if c:
                listing.lat, listing.lng, listing.geo_precision = c[0], c[1], "postcode"
        if listing.lat is None and listing.outcode:
            c = geo.geocode_postcode(session, listing.outcode)
            if c:
                listing.lat, listing.lng, listing.geo_precision = c[0], c[1], "outcode"

    # Bills
    people = profile.group_size
    est = estimate_bills(people, profile.bills, listing.epc, listing.bedrooms)
    listing.bills_pppw_estimate = round(est.per_person_weekly, 2)
    if listing.rent_pppw is not None:
        if listing.bills_included:
            extra = 0.0
        elif listing.bills_pppw_stated:
            extra = listing.bills_pppw_stated
        else:
            extra = listing.bills_pppw_estimate
        # Whole-house rent split across the group even if there's a spare room
        rent_pp = listing.rent_pppw
        if listing.rent_total_pcm and listing.price_source in ("structured", "text-whole"):
            rent_pp = listing.rent_total_pcm / WEEKS_PER_MONTH / people
            listing.rent_pppw = round(rent_pp, 2)
        listing.total_pppw = round(rent_pp + extra, 2)
    else:
        listing.total_pppw = None

    # Commutes
    commutes = []
    if listing.lat is not None:
        commutes = geo.commutes_for(session, listing.lat, listing.lng, profile.all_places(), use_api=use_routing_api)
    listing.commutes = [c.to_dict() for c in commutes]
    listing.worst_commute_min = max((c.minutes for c in commutes), default=None)

    primary = None
    if profile.people and profile.people[0].places:
        primary = geo.resolve_place(session, profile.people[0].places[0])

    was_great = listing.passes_filters and (listing.score or 0) >= profile.email.great_score_threshold
    ev = evaluate(listing, profile, commutes, primary=primary)
    listing.passes_filters = ev.passes
    listing.filter_reasons = ev.reasons
    listing.warnings = ev.warnings
    listing.score = ev.score
    listing.score_breakdown = ev.breakdown
    is_great = ev.passes and ev.score >= profile.email.great_score_threshold
    if is_great and not was_great and listing.duplicate_of_id is None:
        session.add(Event(listing_id=listing.id, kind="great", data={"score": ev.score}))


def rescore_all(session: Session, profile: Profile | None = None) -> int:
    profile = profile or load_profile(session)
    n = 0
    for l in session.scalars(select(Listing)):
        enrich_and_score(session, l, profile)
        n += 1
    dedupe(session)
    return n


# ------------------------------------------------------------------ dedupe

def _street_key(addr: str | None) -> str | None:
    if not addr:
        return None
    first_part = addr.split(",")[0].lower()
    first_part = re.sub(r"^\d+[a-z]?\s+", "", first_part)
    first_part = re.sub(r"\b(flat|apartment)\s+\w+\b", "", first_part)
    return re.sub(r"[^a-z]", "", first_part) or None


def dedupe(session: Session) -> int:
    """Mark the same house listed on several sites (or twice on one) as duplicates.

    Match = same bedrooms and (same street name, or exact coords within 60 m) and
    per-person rent within 8%. The canonical copy is the one with the most photos.
    """
    rows = list(session.scalars(select(Listing).where(Listing.site_status != "removed")))
    groups: list[list[Listing]] = []
    for l in rows:
        placed = False
        for g in groups:
            o = g[0]
            if l.bedrooms != o.bedrooms or l.bedrooms is None:
                continue
            same_place = False
            sk1, sk2 = _street_key(l.address), _street_key(o.address)
            if sk1 and sk1 == sk2 and (l.outcode == o.outcode or not l.outcode or not o.outcode):
                same_place = True
            elif l.geo_precision == "exact" and o.geo_precision == "exact" and l.lat and o.lat:
                same_place = geo.haversine_km(l.lat, l.lng, o.lat, o.lng) < 0.06
            if not same_place:
                continue
            if l.rent_pppw and o.rent_pppw and abs(l.rent_pppw - o.rent_pppw) / o.rent_pppw > 0.08:
                continue
            g.append(l)
            placed = True
            break
        if not placed:
            groups.append([l])
    changed = 0
    for g in groups:
        g.sort(key=lambda x: (-(len(x.photos or [])), x.first_seen))
        canon = g[0]
        if canon.duplicate_of_id is not None:
            canon.duplicate_of_id = None
            changed += 1
        for other in g[1:]:
            if other.duplicate_of_id != canon.id:
                other.duplicate_of_id = canon.id
                changed += 1
            # carry user state across so hiding one hides all
            if other.user_status != "new" and canon.user_status == "new":
                canon.user_status = other.user_status
            canon.favourite = canon.favourite or other.favourite
    return changed


# ------------------------------------------------------------------ run

def _is_candidate(raw: RawListing, profile: Profile) -> bool:
    beds = raw.bedrooms if raw.bedrooms is not None else parse_bedrooms(raw.text_blob)
    return beds is None or beds in profile.allowed_bedrooms


def run_source(source: str, profile: Profile | None = None, max_pages: int | None = None,
               fetch_details: bool | None = None) -> ScrapeRun:
    with session_scope() as session:
        profile = profile or load_profile(session)
        run = ScrapeRun(source=source)
        session.add(run)
        session.flush()
        run_id = run.id
    cfg = profile.sources.get(source)
    pages = max_pages or (cfg.max_pages if cfg else 3)
    details = cfg.fetch_details if (fetch_details is None and cfg) else bool(fetch_details)
    scraper = get_scraper(source)
    found = new = updated = 0
    error = None
    details_done = 0
    seen_ids: set[str] = set()
    try:
        for loc in profile.locations:
            for raw in scraper.search(profile, loc, pages):
                found += 1
                seen_ids.add(raw.source_id)
                with session_scope() as session:
                    existing = session.scalar(select(Listing).where(Listing.source == source, Listing.source_id == raw.source_id))
                    stale = existing is None or existing.detail_fetched_at is None or \
                        existing.detail_fetched_at < utcnow() - timedelta(days=DETAIL_REFRESH_DAYS)
                    if existing is not None and existing.user_status in ("rejected", "hidden"):
                        stale = False
                if details and stale and details_done < MAX_DETAILS_PER_RUN and _is_candidate(raw, profile):
                    try:
                        raw = scraper.fetch_detail(raw)
                        details_done += 1
                    except FileNotFoundError:
                        raw.let_agreed = True
                    except BlockedError:
                        raise
                    except Exception as e:  # one bad detail page shouldn't kill the run
                        log.warning("[%s] detail %s failed: %s", source, raw.url, e)
                with session_scope() as session:
                    listing, is_new = upsert(session, raw, profile)
                    enrich_and_score(session, listing, profile)
                    new += int(is_new)
                    updated += int(not is_new)
        ok = True
    except Exception as e:
        ok = False
        error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}"
        log.error("[%s] run failed: %s", source, e)
    finally:
        scraper.fetcher.close()
    with session_scope() as session:
        if ok and found:
            cutoff = utcnow() - timedelta(hours=REMOVED_AFTER_HOURS)
            for l in session.scalars(select(Listing).where(Listing.source == source, Listing.site_status == "active",
                                                           Listing.last_seen < cutoff)):
                l.site_status = "removed"
                l.passes_filters = False
                session.add(Event(listing_id=l.id, kind="removed"))
        dedupe(session)
        run = session.get(ScrapeRun, run_id)
        run.finished_at, run.ok, run.found, run.new, run.updated = utcnow(), ok, found, new, updated
        run.pages, run.error = scraper.fetcher.requests, error
        session.expunge(run)
    return run


def run_all(profile: Profile | None = None) -> list[ScrapeRun]:
    with session_scope() as session:
        profile = profile or load_profile(session)
    runs = []
    for name in SCRAPERS:
        cfg = profile.sources.get(name)
        if cfg is None or not cfg.enabled:
            continue
        runs.append(run_source(name, profile))
    return runs
