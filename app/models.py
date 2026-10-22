from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(512))

    title: Mapped[str | None] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(String(300))
    area: Mapped[str | None] = mapped_column(String(100), index=True)
    postcode: Mapped[str | None] = mapped_column(String(10))
    outcode: Mapped[str | None] = mapped_column(String(5), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    geo_precision: Mapped[str | None] = mapped_column(String(16))  # exact|postcode|outcode|area

    property_type: Mapped[str | None] = mapped_column(String(50))
    bedrooms: Mapped[int | None] = mapped_column(Integer, index=True)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    furnished: Mapped[str | None] = mapped_column(String(16))
    epc: Mapped[str | None] = mapped_column(String(2))
    student_let: Mapped[bool | None] = mapped_column(Boolean)

    rent_pppw: Mapped[float | None] = mapped_column(Float)
    rent_total_pcm: Mapped[float | None] = mapped_column(Float)
    price_source: Mapped[str | None] = mapped_column(String(32))
    bills_included: Mapped[bool | None] = mapped_column(Boolean)
    bills_pppw_stated: Mapped[float | None] = mapped_column(Float)
    bills_pppw_estimate: Mapped[float | None] = mapped_column(Float)
    total_pppw: Mapped[float | None] = mapped_column(Float, index=True)  # rent + bills (stated or estimated)
    deposit: Mapped[float | None] = mapped_column(Float)
    min_term_months: Mapped[int | None] = mapped_column(Integer)

    available_from: Mapped[date | None] = mapped_column(Date, index=True)
    available_now: Mapped[bool] = mapped_column(Boolean, default=False)

    description: Mapped[str | None] = mapped_column(Text)
    key_features: Mapped[list] = mapped_column(JSON, default=list)
    agent_name: Mapped[str | None] = mapped_column(String(200))
    agent_phone: Mapped[str | None] = mapped_column(String(50))
    photos: Mapped[list] = mapped_column(JSON, default=list)
    floorplans: Mapped[list] = mapped_column(JSON, default=list)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime)

    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    site_status: Mapped[str] = mapped_column(String(16), default="active")  # active|let_agreed|removed
    price_history: Mapped[list] = mapped_column(JSON, default=list)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"), index=True)

    # Derived by the scoring pass
    passes_filters: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    filter_reasons: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    score: Mapped[float | None] = mapped_column(Float, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    commutes: Mapped[list] = mapped_column(JSON, default=list)
    worst_commute_min: Mapped[float | None] = mapped_column(Float)

    # User state
    user_status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    favourite: Mapped[bool] = mapped_column(Boolean, default=False)
    votes: Mapped[dict] = mapped_column(JSON, default=dict)  # {person name: 1 | -1}
    notes: Mapped[str | None] = mapped_column(Text)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime)

    events: Mapped[list["Event"]] = relationship(back_populates="listing", cascade="all, delete-orphan")


USER_STATUSES = ["new", "shortlisted", "contacted", "viewing", "applied", "rejected", "hidden"]


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # new|price_drop|price_rise|let_agreed|removed|back|great
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    listing: Mapped[Listing] = relationship(back_populates="events")


class Recipient(Base):
    __tablename__ = "recipients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_frequency: Mapped[str] = mapped_column(String(16), default="daily")  # off|daily|twice_daily|weekly
    digest_hour: Mapped[int] = mapped_column(Integer, default=18)
    digest_weekday: Mapped[int] = mapped_column(Integer, default=6)  # 0=Mon (weekly only)
    include_new: Mapped[bool] = mapped_column(Boolean, default=True)
    include_price_drops: Mapped[bool] = mapped_column(Boolean, default=True)
    include_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    include_status_changes: Mapped[bool] = mapped_column(Boolean, default=False)  # let agreed / removed for shortlisted
    instant_great: Mapped[bool] = mapped_column(Boolean, default=True)  # email straight away for great new listings
    min_score: Mapped[float] = mapped_column(Float, default=0)
    only_passing: Mapped[bool] = mapped_column(Boolean, default=True)
    max_items: Mapped[int] = mapped_column(Integer, default=20)
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    found: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class EmailLog(Base):
    __tablename__ = "email_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("recipients.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))  # digest|instant|test
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    subject: Mapped[str] = mapped_column(String(300))
    listing_ids: Mapped[list] = mapped_column(JSON, default=list)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)


class GeoCache(Base):
    __tablename__ = "geocache"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RouteCache(Base):
    """Routing API results (minutes) keyed by mode + rounded endpoints."""
    __tablename__ = "route_cache"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    minutes: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
