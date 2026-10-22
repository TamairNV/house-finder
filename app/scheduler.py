"""Background jobs: periodic scraping, digest emails, instant alerts."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from .config import get_settings
from .db import session_scope
from .emailer import run_due_digests, send_instant_alerts
from .models import ScrapeRun
from .pipeline import run_all
from .store import load_profile

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_scrape_lock = threading.Lock()
state = {"last_scrape_started": None, "last_scrape_finished": None, "scraping": False}


def scrape_job(force: bool = False) -> None:
    tz = ZoneInfo(get_settings().timezone)
    with session_scope() as s:
        profile = load_profile(s)
    q0, q1 = profile.scrape_quiet_hours
    hour = datetime.now(tz).hour
    if not force and (q0 <= hour < q1 if q0 <= q1 else (hour >= q0 or hour < q1)):
        log.info("Quiet hours, skipping scrape")
        return
    if not _scrape_lock.acquire(blocking=False):
        log.info("Scrape already running")
        return
    try:
        state.update(scraping=True, last_scrape_started=datetime.now(tz))
        runs = run_all(profile)
        log.info("Scrape finished: %s", ", ".join(f"{r.source}:{'ok' if r.ok else 'FAIL'} {r.new} new" for r in runs))
        with session_scope() as s:
            send_instant_alerts(s, profile)
    finally:
        state.update(scraping=False, last_scrape_finished=datetime.now(tz))
        _scrape_lock.release()


def email_job() -> None:
    with session_scope() as s:
        profile = load_profile(s)
        n = run_due_digests(s, profile)
        if n:
            log.info("Sent %d digest(s)", n)


def trigger_scrape_now() -> bool:
    if _scrape_lock.locked():
        return False
    threading.Thread(target=scrape_job, kwargs={"force": True}, daemon=True).start()
    return True


def first_scrape_time(hours: float, tz: ZoneInfo) -> datetime:
    """Resume the interval from the last finished run so restarts don't trigger an extra scrape."""
    soon = datetime.now(tz) + timedelta(minutes=2)
    with session_scope() as s:
        last = s.scalar(select(func.max(ScrapeRun.finished_at)))
    if last is None:
        return soon
    due = last.replace(tzinfo=timezone.utc).astimezone(tz) + timedelta(hours=hours)
    return max(soon, due)


def start() -> BackgroundScheduler | None:
    global _scheduler
    if not get_settings().scheduler_enabled or _scheduler is not None:
        return _scheduler
    with session_scope() as s:
        hours = load_profile(s).scrape_interval_hours
    tz = ZoneInfo(get_settings().timezone)
    _scheduler = BackgroundScheduler(timezone=tz)
    _scheduler.add_job(scrape_job, "interval", hours=hours, id="scrape", jitter=600,
                       next_run_time=first_scrape_time(hours, tz), max_instances=1, coalesce=True)
    _scheduler.add_job(email_job, "interval", minutes=10, id="email", max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Scheduler started: scrape every %.1fh, email check every 10 min", hours)
    return _scheduler


def reschedule(hours: float) -> None:
    if _scheduler is not None:
        _scheduler.reschedule_job("scrape", trigger="interval", hours=hours, jitter=600)


def next_scrape() -> datetime | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("scrape")
    return job.next_run_time if job else None


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
