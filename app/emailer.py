"""Email digests, instant 'great place' alerts and reminders."""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import EmailLog, Event, Listing, Recipient, utcnow
from .profile import Profile

log = logging.getLogger(__name__)

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
ACTIVE_STATUSES = ("new", "shortlisted", "contacted", "viewing", "applied")


@dataclass
class Digest:
    new: list[Listing] = field(default_factory=list)
    price_drops: list[tuple[Listing, dict]] = field(default_factory=list)
    reminders: list[Listing] = field(default_factory=list)
    status_changes: list[tuple[Listing, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.new or self.price_drops or self.reminders or self.status_changes)

    def listing_ids(self) -> list[int]:
        ids = [l.id for l in self.new] + [l.id for l, _ in self.price_drops] + [l.id for l in self.reminders] + \
              [l.id for l, _ in self.status_changes]
        return list(dict.fromkeys(ids))


def _wanted(l: Listing, r: Recipient) -> bool:
    if l.duplicate_of_id is not None or l.user_status in ("rejected", "hidden"):
        return False
    if r.only_passing and not l.passes_filters:
        return False
    return (l.score or 0) >= r.min_score


def build_digest(session: Session, r: Recipient, profile: Profile, now: datetime | None = None) -> Digest:
    now = now or utcnow()
    since = r.last_digest_at or (now - timedelta(days=1))
    d = Digest()
    events = session.scalars(select(Event).where(Event.created_at > since).order_by(Event.created_at)).all()
    seen: set[int] = set()
    for e in events:
        l = e.listing
        target = session.get(Listing, l.duplicate_of_id) if l.duplicate_of_id else l
        if target is None:
            continue
        if e.kind == "new" and r.include_new and _wanted(target, r) and target.id not in seen:
            d.new.append(target)
            seen.add(target.id)
        elif e.kind == "price_drop" and r.include_price_drops and _wanted(target, r):
            d.price_drops.append((target, e.data))
        elif e.kind in ("let_agreed", "removed") and r.include_status_changes and (
                target.favourite or target.user_status in ("shortlisted", "contacted", "viewing", "applied")):
            d.status_changes.append((target, e.kind))
    d.new.sort(key=lambda l: -(l.score or 0))
    d.new = d.new[: r.max_items]

    if r.include_reminders:
        rules = profile.email
        cutoff = now - timedelta(days=rules.reminder_every_days)
        q = select(Listing).where(
            Listing.passes_filters.is_(True), Listing.duplicate_of_id.is_(None), Listing.site_status == "active",
            Listing.score >= rules.great_score_threshold, Listing.user_status.in_(("new", "shortlisted")),
            Listing.reminders_sent < rules.max_reminders, Listing.first_seen < cutoff,
        ).order_by(Listing.score.desc())
        for l in session.scalars(q):
            if l.id in seen or (l.last_reminded_at and l.last_reminded_at > cutoff):
                continue
            if _wanted(l, r):
                d.reminders.append(l)
        d.reminders = d.reminders[:10]
    return d


def render_digest(d: Digest, r: Recipient, profile: Profile, kind: str = "digest") -> tuple[str, str, str]:
    s = get_settings()
    if kind == "instant":
        top = d.new[0]
        subject = f"🏠 Great place: {top.title or top.address} ({top.score:.0f}/100)"
    else:
        bits = []
        if d.new:
            bits.append(f"{len(d.new)} new")
        if d.price_drops:
            bits.append(f"{len(d.price_drops)} price drop{'s' if len(d.price_drops) > 1 else ''}")
        if d.reminders:
            bits.append(f"{len(d.reminders)} still great")
        if d.status_changes:
            bits.append(f"{len(d.status_changes)} update{'s' if len(d.status_changes) > 1 else ''}")
        subject = f"House finder: {', '.join(bits)}"
    ctx = dict(d=d, r=r, profile=profile, base_url=s.base_url.rstrip("/"), kind=kind)
    html = _env.get_template("email_digest.html").render(**ctx)
    text = _env.get_template("email_digest.txt").render(**ctx)
    return subject, html, text


def send_email(to: str, subject: str, html: str, text: str) -> None:
    s = get_settings()
    if not s.smtp_configured:
        raise RuntimeError("SMTP is not configured (set HF_SMTP_HOST, HF_SMTP_FROM, ...)")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, s.smtp_from, to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if s.smtp_ssl:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ssl.create_default_context(), timeout=30) as smtp:
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(msg)


def _record(session: Session, r: Recipient | None, kind: str, subject: str, ids: list[int], err: str | None) -> None:
    session.add(EmailLog(recipient_id=r.id if r else None, kind=kind, subject=subject, listing_ids=ids,
                         ok=err is None, error=err))


def send_digest(session: Session, r: Recipient, profile: Profile, now: datetime | None = None,
                dry_run: bool = False, force: bool = False) -> tuple[str, str] | None:
    """Build and send one recipient's digest. Returns (subject, html) or None if nothing to send."""
    now = now or utcnow()
    d = build_digest(session, r, profile, now)
    if d.empty and not force:
        r.last_digest_at = now
        return None
    subject, html, text = render_digest(d, r, profile)
    if dry_run:
        return subject, html
    err = None
    try:
        send_email(r.email, subject, html, text)
    except Exception as e:  # noqa: BLE001
        err = str(e)
        log.error("digest to %s failed: %s", r.email, e)
    _record(session, r, "digest", subject, d.listing_ids(), err)
    if err is None:
        r.last_digest_at = now
        for l in d.reminders:
            l.reminders_sent += 1
            l.last_reminded_at = now
    return subject, html


def send_instant_alerts(session: Session, profile: Profile) -> int:
    """Email recipients with instant_great=True about newly 'great' listings."""
    sent = 0
    recipients = session.scalars(select(Recipient).where(Recipient.active.is_(True), Recipient.instant_great.is_(True))).all()
    if not recipients:
        return 0
    events = session.scalars(select(Event).where(Event.kind == "great", Event.created_at > utcnow() - timedelta(days=2))).all()
    for e in events:
        notified = set(e.data.get("notified", []))
        l = e.listing
        if l.duplicate_of_id or l.user_status in ("rejected", "hidden") or not l.passes_filters:
            continue
        for r in recipients:
            if r.id in notified or (l.score or 0) < r.min_score:
                continue
            d = Digest(new=[l])
            subject, html, text = render_digest(d, r, profile, kind="instant")
            err = None
            try:
                send_email(r.email, subject, html, text)
                sent += 1
            except Exception as ex:  # noqa: BLE001
                err = str(ex)
            _record(session, r, "instant", subject, [l.id], err)
            if err is None:
                notified.add(r.id)
        e.data = {**e.data, "notified": sorted(notified)}
    return sent


def digest_due(r: Recipient, now_local: datetime, last_local: datetime | None) -> bool:
    """Is a digest due at local time ``now_local``? Checked every few minutes by the scheduler."""
    if not r.active or r.digest_frequency == "off":
        return False
    hours = [r.digest_hour] if r.digest_frequency != "twice_daily" else sorted({r.digest_hour, (r.digest_hour + 12) % 24})
    if r.digest_frequency == "weekly" and now_local.weekday() != r.digest_weekday:
        return False
    for h in hours:
        slot = now_local.replace(hour=h, minute=0, second=0, microsecond=0)
        if slot <= now_local < slot + timedelta(hours=3) and (last_local is None or last_local < slot):
            return True
    return False


def run_due_digests(session: Session, profile: Profile) -> int:
    tz = ZoneInfo(get_settings().timezone)
    now_local = datetime.now(tz)
    n = 0
    for r in session.scalars(select(Recipient).where(Recipient.active.is_(True))):
        last_local = r.last_digest_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz) if r.last_digest_at else None
        if digest_due(r, now_local, last_local):
            if send_digest(session, r, profile):
                n += 1
    return n
