from datetime import date, timedelta

import pytest

from app import geo
from app.bills import estimate_bills
from app.models import Event, Listing
from app.pipeline import dedupe, enrich_and_score, normalise, upsert
from app.profile import BillsConfig, Person, Place, Profile
from app.scoring import evaluate, price_component
from app.scrapers.base import RawListing

UNI = (54.9803, -1.6157)


def profile(**kw) -> Profile:
    base = dict(group_size=4, budget_pppw=150, budget_buffer_pppw=15,
                people=[Person(name="T", places=[Place(label="Uni", lat=UNI[0], lng=UNI[1], max_minutes=25)])])
    base.update(kw)
    return Profile(**base)


def raw(**kw) -> RawListing:
    d = dict(source="rightmove", source_id="1", url="https://x/1", title="4 bed house", address="Holly Avenue, Jesmond, Newcastle Upon Tyne NE2",
             bedrooms=4, bathrooms=2, lat=54.9883, lng=-1.6023, price_amount=2600, price_period="pcm",
             available_from=date(2027, 7, 1), description="Lovely student house. £150 PPPW", photos=["a", "b"])
    d.update(kw)
    return RawListing(**d)


def test_bills_calibration():
    est = estimate_bills(5, BillsConfig())
    # Fused Bills: 5-bed high usage ~£202/month for energy; ours adds water/broadband/contingency
    assert 190 < est.electricity + est.gas < 220
    assert 15 < estimate_bills(4, BillsConfig()).per_person_weekly < 20
    assert estimate_bills(4, BillsConfig(), epc="G").total_monthly > estimate_bills(4, BillsConfig(), epc="B").total_monthly


def test_normalise_prefers_text_pppw():
    v = normalise(raw(), profile())
    assert v["rent_pppw"] == 150 and v["price_source"] == "text-pppw"
    assert v["area"] == "Jesmond"


def test_normalise_academic_year_hint():
    v = normalise(raw(available_from=None, description="Now letting for 2027/28. £150 PPPW"), profile(), today=date(2026, 10, 22))
    assert v["available_from"] == date(2027, 7, 1)


def test_upsert_events_and_price_drop(session):
    p = profile()
    l, new = upsert(session, raw(), p)
    assert new
    l2, new2 = upsert(session, raw(description="£140 PPPW now reduced"), p)
    assert not new2 and l2.id == l.id and l2.rent_pppw == 140
    kinds = [e.kind for e in session.query(Event).filter_by(listing_id=l.id)]
    assert kinds == ["new", "price_drop"]
    assert [h["pppw"] for h in l2.price_history] == [150, 140]


def test_enrich_scores_and_flags_great(session, monkeypatch):
    p = profile()
    l, _ = upsert(session, raw(description="£110 PPPW bills included"), p)
    enrich_and_score(session, l, p)
    assert l.passes_filters, l.filter_reasons
    assert l.bills_included is True and l.total_pppw == 110
    assert l.commutes[0]["minutes"] < 25
    assert l.score >= 80
    assert session.query(Event).filter_by(listing_id=l.id, kind="great").count() == 1


@pytest.mark.parametrize("override,reason", [
    (dict(bedrooms=5), "5 bedrooms"),
    (dict(bathrooms=1), "1 bathroom(s) for 4 people"),
    (dict(available_from=date.today(), available_now=True), "Available now"),
    (dict(available_from=date(2027, 10, 1)), "after"),
    (dict(description="£200 PPPW"), "max"),
    (dict(lat=55.2, lng=-1.6), "km from"),
])
def test_hard_filters(session, override, reason):
    p = profile()
    l, _ = upsert(session, raw(**override), p)
    enrich_and_score(session, l, p)
    assert not l.passes_filters
    assert any(reason in r for r in l.filter_reasons), l.filter_reasons


def test_bills_estimate_added_when_not_included(session):
    p = profile()
    l, _ = upsert(session, raw(description="£130 PPPW, bills not included"), p)
    enrich_and_score(session, l, p)
    assert l.bills_included is False
    assert l.total_pppw == pytest.approx(130 + l.bills_pppw_estimate, abs=0.01)


def test_stated_bills_package_used(session):
    p = profile()
    l, _ = upsert(session, raw(description="£120PPPW. Compulsory Additional £24PPPW Bills Package"), p)
    enrich_and_score(session, l, p)
    assert l.total_pppw == 144


def test_dedupe_across_sites(session):
    p = profile()
    a, _ = upsert(session, raw(), p)
    b, _ = upsert(session, raw(source="afs", source_id="9", url="https://afs/9", address="Holly Avenue, Jesmond, NE2 2PY",
                               photos=["1", "2", "3"]), p)
    c, _ = upsert(session, raw(source="openrent", source_id="7", url="https://o/7", address="Other Street, Heaton NE6",
                               lat=54.97, lng=-1.57), p)
    dedupe(session)
    assert b.duplicate_of_id is None and a.duplicate_of_id == b.id and c.duplicate_of_id is None


def test_price_component_shape():
    assert price_component(100, 150, 165) == 1.0
    assert price_component(150, 150, 165) == pytest.approx(0.6)
    assert price_component(165, 150, 165) == 0
    assert price_component(170, 150, 165) == 0


def test_estimate_minutes_reasonable():
    # Jesmond (Holly Ave) to Newcastle Uni ~1 km: about a 15 minute walk
    km = geo.haversine_km(54.9883, -1.6023, *UNI)
    assert 10 < geo.estimate_minutes(km, "walk") < 20
    assert geo.estimate_minutes(0.3, "transit") == geo.estimate_minutes(0.3, "walk")


def test_add_missing_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app import db
    url = f"sqlite:///{tmp_path}/old.db"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE recipients (id INTEGER PRIMARY KEY, name VARCHAR(100), email VARCHAR(200))"))
    db._add_missing_columns(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("recipients")}
    assert {"digest_frequency", "instant_great", "min_score"} <= cols


def test_run_source_persists_run_counts(session, monkeypatch):
    # Regression: the ScrapeRun was expunged before commit, so /api/status showed ok=False, 0 found.
    from app import pipeline
    from app.models import ScrapeRun
    from sqlalchemy import select

    class FakeFetcher:
        requests = 3

        def close(self):
            pass

    class FakeScraper:
        fetcher = FakeFetcher()

        def search(self, profile, loc, pages):
            yield RawListing(source="rightmove", source_id="1", url="u1", bedrooms=5, price_amount=130,
                             price_period="pw", price_per_person=True)

    monkeypatch.setattr(pipeline, "get_scraper", lambda name: FakeScraper())
    session.commit()
    pipeline.run_source("rightmove", Profile(), fetch_details=False)
    session.expire_all()
    run = session.scalars(select(ScrapeRun)).one()
    assert run.ok and run.finished_at is not None and run.found == 1 and run.new == 1 and run.pages == 3
