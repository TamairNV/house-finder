"""JSON API used by the web UI."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, ValidationError
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from . import geo, scheduler
from .bills import estimate_bills
from .config import get_settings
from .db import get_session
from .emailer import Digest, render_digest, send_digest, send_email
from .models import USER_STATUSES, EmailLog, Event, Listing, Recipient, ScrapeRun
from .pipeline import rescore_all
from .profile import Profile
from .scrapers.registry import SCRAPERS
from .store import load_profile, save_profile

router = APIRouter(prefix="/api")


def listing_dict(l: Listing, full: bool = False) -> dict[str, Any]:
    d = {
        "id": l.id, "source": l.source, "url": l.url, "title": l.title, "address": l.address, "area": l.area,
        "postcode": l.postcode or l.outcode, "lat": l.lat, "lng": l.lng, "geo_precision": l.geo_precision,
        "bedrooms": l.bedrooms, "bathrooms": l.bathrooms, "furnished": l.furnished,
        "rent_pppw": l.rent_pppw, "rent_total_pcm": l.rent_total_pcm, "total_pppw": l.total_pppw,
        "bills_included": l.bills_included, "bills_pppw_stated": l.bills_pppw_stated,
        "bills_pppw_estimate": l.bills_pppw_estimate,
        "available_from": l.available_from.isoformat() if l.available_from else None,
        "available_now": l.available_now, "photos": l.photos or [], "score": l.score,
        "passes_filters": l.passes_filters, "filter_reasons": l.filter_reasons or [], "warnings": l.warnings or [],
        "commutes": l.commutes or [], "worst_commute_min": l.worst_commute_min,
        "user_status": l.user_status, "favourite": l.favourite, "notes": l.notes,
        "site_status": l.site_status, "first_seen": l.first_seen.isoformat() if l.first_seen else None,
        "duplicate_of_id": l.duplicate_of_id, "agent_name": l.agent_name,
    }
    if full:
        d.update({
            "description": l.description, "key_features": l.key_features or [], "floorplans": l.floorplans or [],
            "deposit": l.deposit, "min_term_months": l.min_term_months, "epc": l.epc, "agent_phone": l.agent_phone,
            "score_breakdown": l.score_breakdown or {}, "price_history": l.price_history or [],
            "price_source": l.price_source, "property_type": l.property_type, "student_let": l.student_let,
            "last_seen": l.last_seen.isoformat() if l.last_seen else None,
            "detail_fetched_at": l.detail_fetched_at.isoformat() if l.detail_fetched_at else None,
            "card_text": (l.raw or {}).get("card_text", "")[:1500],
        })
    return d


SORTS = {
    "score": (desc(Listing.score),),
    "price": (Listing.total_pppw.is_(None), Listing.total_pppw),
    "price_desc": (desc(Listing.total_pppw),),
    "newest": (desc(Listing.first_seen),),
    "commute": (Listing.worst_commute_min.is_(None), Listing.worst_commute_min),
    "available": (Listing.available_from.is_(None), Listing.available_from),
    "bathrooms": (desc(Listing.bathrooms),),
}


@router.get("/listings")
def list_listings(
    s: Session = Depends(get_session),
    q: str | None = None,
    passing: bool = True,
    include_duplicates: bool = False,
    include_inactive: bool = False,
    status: list[str] = Query(default=[]),
    source: list[str] = Query(default=[]),
    area: list[str] = Query(default=[]),
    min_score: float | None = None,
    max_pppw: float | None = None,
    min_bathrooms: float | None = None,
    bills_included: bool | None = None,
    favourite: bool | None = None,
    max_commute: float | None = None,
    available_after: date | None = None,
    available_before: date | None = None,
    has_photos: bool | None = None,
    sort: str = "score",
    limit: int = Query(300, le=2000),
):
    stmt = select(Listing)
    if passing:
        stmt = stmt.where(Listing.passes_filters.is_(True))
    if not include_duplicates:
        stmt = stmt.where(Listing.duplicate_of_id.is_(None))
    if not include_inactive:
        stmt = stmt.where(Listing.site_status == "active")
    if status:
        stmt = stmt.where(Listing.user_status.in_(status))
    else:
        stmt = stmt.where(Listing.user_status.notin_(("hidden", "rejected")))
    if source:
        stmt = stmt.where(Listing.source.in_(source))
    if area:
        stmt = stmt.where(or_(*[Listing.area.ilike(f"%{a}%") | Listing.address.ilike(f"%{a}%") for a in area]))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Listing.title.ilike(like), Listing.address.ilike(like), Listing.description.ilike(like),
                              Listing.notes.ilike(like)))
    if min_score is not None:
        stmt = stmt.where(Listing.score >= min_score)
    if max_pppw is not None:
        stmt = stmt.where(Listing.total_pppw <= max_pppw)
    if min_bathrooms is not None:
        stmt = stmt.where(Listing.bathrooms >= min_bathrooms)
    if bills_included is not None:
        stmt = stmt.where(Listing.bills_included.is_(bills_included))
    if favourite:
        stmt = stmt.where(Listing.favourite.is_(True))
    if max_commute is not None:
        stmt = stmt.where(Listing.worst_commute_min <= max_commute)
    if available_after:
        stmt = stmt.where(Listing.available_from >= available_after)
    if available_before:
        stmt = stmt.where(Listing.available_from <= available_before)
    if has_photos:
        stmt = stmt.where(func.json_array_length(Listing.photos) > 0)
    stmt = stmt.order_by(*SORTS.get(sort, SORTS["score"]), desc(Listing.id)).limit(limit)
    rows = s.scalars(stmt).all()
    # duplicates map: canonical id -> other sources
    ids = [l.id for l in rows]
    dups: dict[int, list[dict]] = {}
    if ids:
        for d in s.scalars(select(Listing).where(Listing.duplicate_of_id.in_(ids))):
            dups.setdefault(d.duplicate_of_id, []).append({"id": d.id, "source": d.source, "url": d.url,
                                                           "total_pppw": d.total_pppw})
    out = []
    for l in rows:
        d = listing_dict(l)
        d["also_on"] = dups.get(l.id, [])
        out.append(d)
    return {"items": out, "count": len(out)}


@router.get("/listings/{listing_id}")
def get_listing(listing_id: int, s: Session = Depends(get_session)):
    l = s.get(Listing, listing_id)
    if not l:
        raise HTTPException(404)
    d = listing_dict(l, full=True)
    d["also_on"] = [{"id": x.id, "source": x.source, "url": x.url, "total_pppw": x.total_pppw}
                    for x in s.scalars(select(Listing).where(Listing.duplicate_of_id == l.id))]
    if l.duplicate_of_id:
        d["canonical"] = {"id": l.duplicate_of_id}
    d["events"] = [{"kind": e.kind, "at": e.created_at.isoformat(), "data": e.data}
                   for e in sorted(l.events, key=lambda e: e.created_at, reverse=True)[:30]]
    profile = load_profile(s)
    d["bills_breakdown"] = estimate_bills(profile.group_size, profile.bills, l.epc, l.bedrooms).to_dict()
    return d


class ListingPatch(BaseModel):
    user_status: Literal[tuple(USER_STATUSES)] | None = None  # type: ignore[valid-type]
    favourite: bool | None = None
    notes: str | None = None


@router.patch("/listings/{listing_id}")
def patch_listing(listing_id: int, patch: ListingPatch, s: Session = Depends(get_session)):
    l = s.get(Listing, listing_id)
    if not l:
        raise HTTPException(404)
    targets = [l] + list(s.scalars(select(Listing).where(Listing.duplicate_of_id == l.id)))
    for t in targets:
        for k, v in patch.model_dump(exclude_none=True).items():
            if k == "notes" and t is not l:
                continue
            setattr(t, k, v)
    return listing_dict(l)


@router.get("/meta")
def meta(s: Session = Depends(get_session)):
    areas = s.execute(select(Listing.area, func.count()).where(Listing.area.is_not(None), Listing.site_status == "active")
                      .group_by(Listing.area).order_by(desc(func.count())).limit(40)).all()
    counts = dict(s.execute(select(Listing.source, func.count()).group_by(Listing.source)).all())
    return {
        "sources": [{"name": k, "label": v.label, "count": counts.get(k, 0)} for k, v in SCRAPERS.items()],
        "areas": [{"name": a, "count": c} for a, c in areas],
        "statuses": USER_STATUSES,
        "totals": {
            "all": s.scalar(select(func.count()).select_from(Listing)),
            "passing": s.scalar(select(func.count()).select_from(Listing).where(
                Listing.passes_filters.is_(True), Listing.duplicate_of_id.is_(None))),
        },
    }


# ---------------------------------------------------------------- profile

@router.get("/profile")
def get_profile(s: Session = Depends(get_session)):
    p = load_profile(s)
    return {"profile": p.model_dump(mode="json"), "bills_example": estimate_bills(p.group_size, p.bills).to_dict()}


@router.put("/profile")
def put_profile(body: dict, s: Session = Depends(get_session)):
    try:
        p = Profile.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False))
    old = load_profile(s)
    save_profile(s, p)
    if p.scrape_interval_hours != old.scrape_interval_hours:
        scheduler.reschedule(p.scrape_interval_hours)
    n = rescore_all(s, p)
    return {"ok": True, "rescored": n}


@router.get("/geocode")
def geocode(postcode: str, s: Session = Depends(get_session)):
    c = geo.geocode_postcode(s, postcode)
    if not c:
        raise HTTPException(404, "postcode not found")
    return {"lat": c[0], "lng": c[1]}


@router.post("/rescore")
def rescore(s: Session = Depends(get_session)):
    return {"rescored": rescore_all(s)}


# ---------------------------------------------------------------- recipients

class RecipientIn(BaseModel):
    name: str
    email: EmailStr
    active: bool = True
    digest_frequency: Literal["off", "daily", "twice_daily", "weekly"] = "daily"
    digest_hour: int = 18
    digest_weekday: int = 6
    include_new: bool = True
    include_price_drops: bool = True
    include_reminders: bool = True
    include_status_changes: bool = False
    instant_great: bool = True
    min_score: float = 0
    only_passing: bool = True
    max_items: int = 20


def recipient_dict(r: Recipient) -> dict:
    d = {c.name: getattr(r, c.name) for c in Recipient.__table__.columns}
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


@router.get("/recipients")
def list_recipients(s: Session = Depends(get_session)):
    return [recipient_dict(r) for r in s.scalars(select(Recipient).order_by(Recipient.id))]


@router.post("/recipients")
def create_recipient(body: RecipientIn, s: Session = Depends(get_session)):
    r = Recipient(**body.model_dump())
    s.add(r)
    s.flush()
    return recipient_dict(r)


@router.put("/recipients/{rid}")
def update_recipient(rid: int, body: RecipientIn, s: Session = Depends(get_session)):
    r = s.get(Recipient, rid)
    if not r:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    return recipient_dict(r)


@router.delete("/recipients/{rid}")
def delete_recipient(rid: int, s: Session = Depends(get_session)):
    r = s.get(Recipient, rid)
    if r:
        s.delete(r)
    return {"ok": True}


@router.get("/recipients/{rid}/preview", response_class=HTMLResponse)
def preview_digest(rid: int, s: Session = Depends(get_session)):
    r = s.get(Recipient, rid)
    if not r:
        raise HTTPException(404)
    profile = load_profile(s)
    from .emailer import build_digest
    d = build_digest(s, r, profile)
    if d.empty:
        # show the current top matches so the layout can still be checked
        d = Digest(new=list(s.scalars(select(Listing).where(Listing.passes_filters.is_(True), Listing.duplicate_of_id.is_(None))
                                      .order_by(desc(Listing.score)).limit(5))))
    _, html, _ = render_digest(d, r, profile)
    return html


@router.post("/recipients/{rid}/send")
def send_now(rid: int, s: Session = Depends(get_session)):
    r = s.get(Recipient, rid)
    if not r:
        raise HTTPException(404)
    if not get_settings().smtp_configured:
        raise HTTPException(400, "SMTP is not configured on the server (.env)")
    res = send_digest(s, r, load_profile(s), force=True)
    log = s.scalars(select(EmailLog).where(EmailLog.recipient_id == r.id).order_by(desc(EmailLog.id))).first()
    if log and not log.ok:
        raise HTTPException(502, log.error)
    return {"ok": True, "subject": res[0] if res else None}


@router.post("/email/test")
def test_email(to: EmailStr):
    try:
        send_email(to, "House finder test email", "<p>SMTP works 🎉</p>", "SMTP works")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e))
    return {"ok": True}


# ---------------------------------------------------------------- status / scraping

@router.post("/scrape")
def scrape_now():
    started = scheduler.trigger_scrape_now()
    return {"started": started, "message": "Scrape started" if started else "A scrape is already running"}


@router.get("/status")
def status(s: Session = Depends(get_session)):
    runs = s.scalars(select(ScrapeRun).order_by(desc(ScrapeRun.id)).limit(40)).all()
    emails = s.scalars(select(EmailLog).order_by(desc(EmailLog.id)).limit(30)).all()
    nxt = scheduler.next_scrape()
    st = get_settings()
    return {
        "scraping": scheduler.state["scraping"],
        "next_scrape": nxt.isoformat() if nxt else None,
        "smtp_configured": st.smtp_configured,
        "routing_api": bool(st.ors_api_key),
        "playwright": st.playwright_enabled,
        "runs": [{"id": r.id, "source": r.source, "started_at": r.started_at.isoformat(),
                  "finished_at": r.finished_at.isoformat() if r.finished_at else None, "ok": r.ok,
                  "requests": r.pages, "found": r.found, "new": r.new, "updated": r.updated,
                  "error": (r.error or "")[:600]} for r in runs],
        "emails": [{"sent_at": e.sent_at.isoformat(), "kind": e.kind, "subject": e.subject, "ok": e.ok,
                    "error": e.error, "count": len(e.listing_ids or [])} for e in emails],
        "events_24h": dict(s.execute(select(Event.kind, func.count()).where(
            Event.created_at > func.datetime("now", "-1 day")).group_by(Event.kind)).all()),
    }
