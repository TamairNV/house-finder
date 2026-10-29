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


def test_first_scrape_time_resumes_interval(session):
    from datetime import datetime, timezone as tzmod
    from zoneinfo import ZoneInfo

    from app.models import ScrapeRun
    from app.scheduler import first_scrape_time

    tz = ZoneInfo("Europe/London")
    now = datetime.now(tz)
    assert first_scrape_time(4, tz) - now < timedelta(minutes=3)  # no runs yet: soon
    session.add(ScrapeRun(source="rightmove", finished_at=datetime.now(tzmod.utc).replace(tzinfo=None) - timedelta(hours=1)))
    session.commit()
    assert timedelta(hours=2, minutes=55) < first_scrape_time(4, tz) - now < timedelta(hours=3, minutes=5)


def test_rescore_batches_routing_calls(session, monkeypatch):
    from app import geo
    from app.config import get_settings
    from sqlalchemy import select
    from app.pipeline import rescore_all
    monkeypatch.setattr(get_settings(), "ors_api_key", "k")
    calls = []

    def fake_ors(origin, dests, mode):
        calls.append(len(dests))
        return [12.0] * len(dests)

    monkeypatch.setattr(geo, "ors_minutes", fake_ors)
    p = profile(use_routing_api=True)
    for i in range(5):
        upsert(session, raw(source_id=str(i), url=f"https://x/{i}", lat=54.98 + i / 1000), p)
    for l in session.scalars(select(Listing)):
        l.geo_precision = "exact"
    rescore_all(session, p)
    assert calls == [5]  # one matrix call for all five houses, then everything comes from the cache
    l = session.scalars(select(Listing)).first()
    assert l.commutes[0]["minutes"] == 12.0 and not l.commutes[0]["estimated"]
    rescore_all(session, p)
    assert calls == [5]


def test_sitemap_stubs_only_stored_when_live_and_priced(session, monkeypatch):
    from sqlalchemy import select
    from app import pipeline

    class FakeFetcher:
        requests = 0

        def close(self):
            pass

    def stub(i):
        return RawListing(source="sturents", source_id=str(i), url=f"u{i}", raw={"stub": True})

    class FakeScraper:
        fetcher = FakeFetcher()

        def search(self, profile, loc, pages):
            yield from (stub(i) for i in (1, 2, 3))

        def fetch_detail(self, r):
            r.detail_fetched = True
            if r.source_id == "1":  # live, priced
                r.bedrooms, r.price_amount, r.price_period, r.price_per_person = 4, 120, "pw", True
            elif r.source_id == "2":  # "Property unavailable"
                r.bedrooms, r.let_agreed = 4, True
            else:
                raise RuntimeError("boom")  # detail failed: nothing to show
            return r

    monkeypatch.setattr(pipeline, "get_scraper", lambda name: FakeScraper())
    p = profile()
    session.commit()
    pipeline.run_source("sturents", p, fetch_details=True)
    session.expire_all()
    assert [l.source_id for l in session.scalars(select(Listing))] == ["1"]
    # Next run without a detail fetch: the stored one stays alive and keeps its details.
    pipeline.run_source("sturents", p, fetch_details=False)
    session.expire_all()
    l = session.scalars(select(Listing)).one()
    assert l.bedrooms == 4 and l.rent_pppw == 120 and l.site_status == "active"


def test_ors_pauses_on_quota_exceeded(monkeypatch):
    import httpx
    from app import geo
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ors_api_key", "k")
    monkeypatch.setattr(geo, "_ors_paused_until", 0.0)
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return httpx.Response(403, text='{"error":"Quota exceeded"}', request=httpx.Request("POST", url))

    monkeypatch.setattr(geo.httpx, "post", fake_post)
    assert geo.ors_minutes((54.98, -1.61), [(54.97, -1.62)], "walk") is None
    assert geo.ors_minutes((54.98, -1.61), [(54.97, -1.62)], "walk") is None
    assert len(calls) == 1


def test_dedupe_matches_abbreviated_streets_and_copied_text(session):
    p = profile()
    desc = ("A fantastic four bedroom student house on a quiet street close to the Metro, with two bathrooms, a big "
            "modern kitchen, a garden and a lounge. Available July 2027. Contact us to book a viewing now.")
    a, _ = upsert(session, raw(address="Flat 1, 14 Holly Ave, Jesmond, NE2", description=desc + " £150 PPPW"), p)
    # same house, other site: different bedroom count and bills handling, no coords
    b, _ = upsert(session, raw(source="afs", source_id="9", url="https://afs/9", address="Holly Avenue, Jesmond",
                               bedrooms=5, lat=None, lng=None, description=desc + " £175 PPPW inc bills"), p)
    # neighbour: same street, same price, different house number
    c, _ = upsert(session, raw(source="openrent", source_id="7", url="https://o/7", address="16 Holly Avenue, NE2",
                               description="Four bed house £150 PPPW"), p)
    dedupe(session)
    assert {a.duplicate_of_id, b.duplicate_of_id} - {None} in ({a.id}, {b.id})
    assert c.duplicate_of_id is None


def test_dedupe_ignores_agent_boilerplate(session):
    p = profile()
    blurb = ("Hive Estates are pleased to offer this property to let. Please contact our office to arrange a viewing "
             "and ask about our other properties in Jesmond, Heaton and Sandyford, we look forward to hearing from you.")
    rows = [upsert(session, raw(source="hive", source_id=str(i), url=f"https://h/{i}", address=f"{s}, Newcastle",
                                lat=None, lng=None, description=blurb + " £150 PPPW"), p)[0]
            for i, s in enumerate(["Holly Avenue", "Simonside Terrace", "Warwick Street", "Cardigan Terrace"])]
    dedupe(session)
    assert all(r.duplicate_of_id is None for r in rows)


def test_reprice_fixes_bills_taken_as_rent(session):
    p = profile()
    l, _ = upsert(session, raw(description="Bills: £35pppw. Great house."), p)
    assert l.rent_pppw == 150 and l.bills_pppw_stated == 35
    l.rent_pppw, l.bills_pppw_stated = 35, None  # what the old parser stored
    l.raw = {k: v for k, v in l.raw.items() if k != "price"}  # saved before structured prices were kept
    from app.pipeline import reprice
    assert reprice(l, p) and l.rent_pppw is None  # cleared: next scrape refills it
    l.raw = {**l.raw, "price": [2600, "pcm", False]}
    assert reprice(l, p) and l.rent_pppw == 150 and l.bills_pppw_stated == 35


def test_dedupe_does_not_chain_neighbouring_streets(session):
    # Seen live: near-identical 5 beds on Helmsley Road / Doncaster Road snowballed into one 18-listing group.
    p = profile()
    a, _ = upsert(session, raw(address="Helmsley Road, Sandyford NE2", lat=54.9850, lng=-1.6000), p)
    b, _ = upsert(session, raw(source="afs", source_id="2", url="u2", address="Doncaster Road, Sandyford NE2",
                               lat=54.9853, lng=-1.6002), p)
    c, _ = upsert(session, raw(source="unihomes", source_id="3", url="u3", address="Sandyford, Newcastle NE2",
                               lat=54.9851, lng=-1.6001), p)
    dedupe(session)
    assert a.duplicate_of_id is None and b.duplicate_of_id is None  # different streets never merge
    assert c.duplicate_of_id in (a.id, b.id) or c.duplicate_of_id is None
    assert len({x.duplicate_of_id or x.id for x in (a, b, c)}) >= 2


def test_dedupe_keeps_units_in_one_block_apart(session):
    p = profile()
    a, _ = upsert(session, raw(source="spareroom", source_id="1", url="u1", address="50-54 Union Street, NE2"), p)
    b, _ = upsert(session, raw(source="spareroom", source_id="2", url="u2", address="Union Street, NE2"), p)
    dedupe(session)
    assert a.duplicate_of_id is None and b.duplicate_of_id is None


def test_small_flat_rent_split_by_bedrooms_not_group(session):
    p = profile(group_size=5)
    l, _ = upsert(session, raw(bedrooms=2, price_amount=1300, description="Two bed flat"), p)
    enrich_and_score(session, l, p)
    assert l.rent_pppw == 150
