"""Background jobs for every site: periodic scraping, digest emails, instant alerts.

One "tick" job checks each site every few minutes and scrapes the ones that are due, one site at a time
(so listing sites only ever see one of our scrapers at once).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from . import tenancy
from .config import get_settings
from .db import init_db, session_scope
from .emailer import run_due_digests, send_instant_alerts
from .models import ScrapeRun
from .pipeline import run_all
from .store import load_profile

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_scrape_lock = threading.Lock()
_running: set[str] = set()  # slugs being scraped right now
state: dict[str, dict] = {}  # slug -> {"last_scrape_started", "last_scrape_finished"}
TICK_MINUTES = 5


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _quiet(profile, now: datetime) -> bool:
    q0, q1 = profile.scrape_quiet_hours
    hour = now.hour
    return q0 <= hour < q1 if q0 <= q1 else (hour >= q0 or hour < q1)


def _last_finished() -> datetime | None:
    with session_scope() as s:
        last = s.scalar(select(func.max(ScrapeRun.finished_at)))
    return last.replace(tzinfo=timezone.utc).astimezone(_tz()) if last else None


def scrape_job(force: bool = False, site: tenancy.SiteInfo | None = None) -> bool:
    """Scrape one site (default: the current one). Returns False if skipped."""
    site = site or tenancy.current()
    with tenancy.use(site):
        with session_scope() as s:
            profile = load_profile(s)
        now = datetime.now(_tz())
        if not force and _quiet(profile, now):
            log.info("[%s] quiet hours, skipping scrape", site.slug)
            return False
        if not _scrape_lock.acquire(blocking=False):
            log.info("Scrape already running")
            return False
        try:
            _running.add(site.slug)
            state.setdefault(site.slug, {})["last_scrape_started"] = now
            runs = run_all(profile)
            log.info("[%s] scrape finished: %s", site.slug,
                     ", ".join(f"{r.source}:{'ok' if r.ok else 'FAIL'} {r.new} new" for r in runs))
            with session_scope() as s:
                send_instant_alerts(s, profile)
        finally:
            _running.discard(site.slug)
            state.setdefault(site.slug, {})["last_scrape_finished"] = datetime.now(_tz())
            _scrape_lock.release()
    return True


def due(site: tenancy.SiteInfo, now: datetime | None = None) -> bool:
    with tenancy.use(site):
        with session_scope() as s:
            profile = load_profile(s)
        now = now or datetime.now(_tz())
        if _quiet(profile, now):
            return False
        last = _last_finished()
        return last is None or now >= last + timedelta(hours=profile.scrape_interval_hours)


def tick() -> None:
    """Scrape every site that's due, one after another."""
    for site in tenancy.all_sites(include_disabled=False):
        try:
            if due(site):
                scrape_job(site=site)
        except Exception:  # one broken site shouldn't stop the others
            log.exception("[%s] scheduled scrape failed", site.slug)


def email_job() -> None:
    for site in tenancy.all_sites(include_disabled=False):
        try:
            with tenancy.use(site), session_scope() as s:
                n = run_due_digests(s, load_profile(s))
                if n:
                    log.info("[%s] sent %d digest(s)", site.slug, n)
        except Exception:
            log.exception("[%s] digest job failed", site.slug)


def prune_job() -> None:
    from . import admin_db
    admin_db.prune()


def trigger_scrape_now(site: tenancy.SiteInfo | None = None) -> bool:
    if _scrape_lock.locked():
        return False
    site = site or tenancy.current()
    threading.Thread(target=scrape_job, kwargs={"force": True, "site": site}, daemon=True).start()
    return True


def first_scrape_time(hours: float, tz: ZoneInfo) -> datetime:
    """When the current site's next scrape is due (resumes the interval from the last finished run)."""
    soon = datetime.now(tz) + timedelta(minutes=2)
    last = _last_finished()
    if last is None:
        return soon
    return max(soon, last.astimezone(tz) + timedelta(hours=hours))


def next_scrape() -> datetime | None:
    """Roughly when the current site scrapes next (None if the scheduler is off)."""
    if _scheduler is None:
        return None
    with session_scope() as s:
        hours = load_profile(s).scrape_interval_hours
    return first_scrape_time(hours, _tz())


def is_scraping(site: tenancy.SiteInfo | None = None) -> bool:
    return (site or tenancy.current()).slug in _running


def init_sites() -> None:
    """Create/upgrade every site's tables (cheap when nothing changed)."""
    for site in tenancy.all_sites():
        try:
            with tenancy.use(site):
                init_db()
        except Exception:
            log.exception("[%s] couldn't open its database", site.slug)


def start() -> BackgroundScheduler | None:
    global _scheduler
    if not get_settings().scheduler_enabled or _scheduler is not None:
        return _scheduler
    tz = _tz()
    _scheduler = BackgroundScheduler(timezone=tz)
    _scheduler.add_job(tick, "interval", minutes=TICK_MINUTES, id="scrape", max_instances=1, coalesce=True,
                       next_run_time=datetime.now(tz) + timedelta(minutes=2))
    _scheduler.add_job(email_job, "interval", minutes=10, id="email", max_instances=1, coalesce=True)
    _scheduler.add_job(prune_job, "interval", hours=24, id="prune", max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Scheduler started: checking %d site(s) every %d min, email every 10 min",
             len(tenancy.all_sites(include_disabled=False)), TICK_MINUTES)
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
