from datetime import datetime, timedelta

from app.emailer import build_digest, digest_due, render_digest, send_digest
from app.models import Listing, Recipient, utcnow
from app.pipeline import enrich_and_score, upsert
from tests.test_pipeline import profile, raw


def test_digest_contents_and_rendering(session):
    p = profile()
    good, _ = upsert(session, raw(description="£110 PPPW bills included"), p)
    enrich_and_score(session, good, p)
    bad, _ = upsert(session, raw(source_id="2", bedrooms=6), p)
    enrich_and_score(session, bad, p)
    r = Recipient(name="T", email="t@example.com", last_digest_at=utcnow() - timedelta(hours=1))
    session.add(r)
    session.flush()
    d = build_digest(session, r, p)
    assert [l.id for l in d.new] == [good.id]  # failing listing excluded (only_passing)
    subject, html, text = render_digest(d, r, p)
    assert "1 new" in subject and "£110" in html and good.url in text


def test_reminders_for_great_unactioned(session):
    p = profile()
    l, _ = upsert(session, raw(description="£110 PPPW bills included"), p)
    enrich_and_score(session, l, p)
    l.first_seen = utcnow() - timedelta(days=5)
    session.flush()  # events get their timestamp at flush
    r = Recipient(name="T", email="t@example.com", last_digest_at=utcnow() + timedelta(seconds=1))
    session.add(r)
    session.flush()
    d = build_digest(session, r, p)
    assert d.reminders == [l] and not d.new
    l.user_status = "rejected"
    assert build_digest(session, r, p).reminders == []


def test_send_digest_dry_run_and_failure_logged(session, monkeypatch):
    p = profile()
    l, _ = upsert(session, raw(description="£110 PPPW bills included"), p)
    enrich_and_score(session, l, p)
    r = Recipient(name="T", email="t@example.com")
    session.add(r)
    session.flush()
    subject, html = send_digest(session, r, p, dry_run=True)
    assert "House finder" in subject
    sent = []
    monkeypatch.setattr("app.emailer.send_email", lambda *a: sent.append(a))
    send_digest(session, r, p)
    assert sent and r.last_digest_at is not None


def test_digest_due_windows():
    r = Recipient(name="x", email="x@y.z", digest_frequency="daily", digest_hour=18, active=True, digest_weekday=6)
    at = lambda h, d=22: datetime(2026, 10, d, h, 5)
    assert not digest_due(r, at(17), None)
    assert digest_due(r, at(18), None)
    assert not digest_due(r, at(19), at(18))  # already sent today
    assert digest_due(r, at(18, 23), at(18))  # next day
    r.digest_frequency = "twice_daily"
    assert digest_due(r, at(6), at(18, 21))
    r.digest_frequency = "weekly"  # Sunday only
    assert not digest_due(r, at(18), None)  # 22 Oct 2026 is a Thursday
    assert digest_due(r, at(18, 25), None)
    r.digest_frequency = "off"
    assert not digest_due(r, at(18), None)
