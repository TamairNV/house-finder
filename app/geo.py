"""Geocoding (postcodes.io, free, no key) and commute-time estimates."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import GeoCache, RouteCache
from .profile import Place

log = logging.getLogger(__name__)

POSTCODES_IO = "https://api.postcodes.io"

# Rough door-to-door speeds used when no routing API is configured.
# detour = road distance / straight-line distance, typical for UK cities.
SPEEDS_KMH = {"walk": 5.0, "cycle": 15.0, "transit": 16.0, "drive": 22.0}
DETOUR = {"walk": 1.2, "cycle": 1.25, "transit": 1.35, "drive": 1.4}
OVERHEAD_MIN = {"walk": 0.0, "cycle": 2.0, "transit": 8.0, "drive": 5.0}  # waiting, parking, locking bike


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_minutes(km: float, mode: str) -> float:
    road_km = km * DETOUR[mode]
    t = road_km / SPEEDS_KMH[mode] * 60 + OVERHEAD_MIN[mode]
    if mode == "transit":
        # Short hops are quicker on foot; never estimate transit slower than walking.
        t = min(t, estimate_minutes(km, "walk"))
    return round(t, 1)


@dataclass
class Commute:
    person: str
    place: str
    mode: str
    km: float
    minutes: float
    max_minutes: int
    hard_limit: bool
    estimated: bool  # True = distance-based estimate, False = routing API
    weight: float

    def to_dict(self) -> dict:
        return asdict(self)


def _cache_get(session: Session, key: str) -> tuple[float | None, float | None] | None:
    row = session.get(GeoCache, key)
    return (row.lat, row.lng) if row else None


def _cache_put(session: Session, key: str, lat: float | None, lng: float | None) -> None:
    session.merge(GeoCache(key=key, lat=lat, lng=lng))


_failed_until: dict[str, float] = {}  # postcode -> monotonic time; avoids hammering a down API


def geocode_postcode(session: Session, postcode: str, client: httpx.Client | None = None) -> tuple[float, float] | None:
    """Full postcode or outcode (e.g. 'NE2') -> (lat, lng). Cached forever."""
    pc = postcode.strip().upper()
    key = f"pc:{pc}"
    cached = _cache_get(session, key)
    if cached is not None:
        return cached if cached[0] is not None else None
    if _failed_until.get(pc, 0) > time.monotonic():
        return None
    is_outcode = " " not in pc and len(pc) <= 4
    url = f"{POSTCODES_IO}/outcodes/{pc}" if is_outcode else f"{POSTCODES_IO}/postcodes/{pc.replace(' ', '')}"
    own = client is None
    client = client or httpx.Client(timeout=15)
    try:
        r = client.get(url)
        if r.status_code == 404:
            _cache_put(session, key, None, None)
            return None
        r.raise_for_status()
        res = r.json().get("result") or {}
        lat, lng = res.get("latitude"), res.get("longitude")
        _cache_put(session, key, lat, lng)
        return (lat, lng) if lat is not None else None
    except httpx.HTTPError as e:
        log.warning("postcodes.io lookup failed for %s: %s", pc, e)
        _failed_until[pc] = time.monotonic() + 600
        return None
    finally:
        if own:
            client.close()


def resolve_place(session: Session, place: Place) -> tuple[float, float] | None:
    if place.lat is not None and place.lng is not None:
        return place.lat, place.lng
    if place.postcode:
        return geocode_postcode(session, place.postcode)
    return None


ORS_PROFILES = {"walk": "foot-walking", "cycle": "cycling-regular", "drive": "driving-car"}


ORS_CHUNK = 200  # destinations per matrix call (free plan allows 3500 elements per request)
_ors_paused_until = 0.0  # set after a 429 so a rescore doesn't hammer the API


def ors_minutes(origin: tuple[float, float], dests: list[tuple[float, float]], mode: str) -> list[float | None] | None:
    """openrouteservice matrix call (free key: 500 matrix requests/day, 40/minute)."""
    global _ors_paused_until
    key = get_settings().ors_api_key
    if not key or mode not in ORS_PROFILES or not dests or time.time() < _ors_paused_until:
        return None
    body = {"locations": [[origin[1], origin[0]]] + [[d[1], d[0]] for d in dests],
            "sources": [0], "destinations": list(range(1, len(dests) + 1)), "metrics": ["duration"]}
    try:
        r = httpx.post(f"https://api.openrouteservice.org/v2/matrix/{ORS_PROFILES[mode]}",
                       json=body, headers={"Authorization": key}, timeout=30)
        if r.status_code == 429:
            _ors_paused_until = time.time() + 300
            log.warning("ORS rate limit hit; using distance estimates for 5 minutes")
            return None
        if r.status_code == 403 and "quota" in r.text.lower():
            _ors_paused_until = time.time() + 3 * 3600  # daily allowance used up: don't keep trying all day
            log.warning("ORS daily quota used up; using distance estimates for 3 hours")
            return None
        r.raise_for_status()
        durs = r.json()["durations"][0]
        return [round(d / 60, 1) if d is not None else None for d in durs]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("ORS matrix failed: %s", e)
        return None


def _route_key(origin: tuple[float, float], dest: tuple[float, float], mode: str) -> str:
    return f"{mode}:{origin[0]:.4f},{origin[1]:.4f}>{dest[0]:.4f},{dest[1]:.4f}"


def prewarm_routes(session: Session, origins: list[tuple[float, float]], people_places: list) -> int:
    """Fill the route cache for many houses at once: one matrix call per place (per 200 houses) instead of one
    call per house, which keeps a full re-score well inside the free ORS limits. Returns API calls made."""
    calls = 0
    for _, place in people_places:
        coords = resolve_place(session, place)
        if coords is None or place.mode not in ORS_PROFILES:
            continue
        missing = {_route_key(o, coords, place.mode): o for o in origins}
        missing = {k: o for k, o in missing.items() if session.get(RouteCache, k) is None}
        items = list(missing.items())
        for i in range(0, len(items), ORS_CHUNK):
            chunk = items[i:i + ORS_CHUNK]
            # Place -> houses; walking/cycling times are near enough symmetric for a commute estimate.
            res = ors_minutes(coords, [o for _, o in chunk], place.mode)
            calls += 1
            if res is None:
                return calls
            for (k, _), m in zip(chunk, res):
                session.merge(RouteCache(key=k, minutes=m))
        session.flush()
    return calls


def cached_route_minutes(session: Session, origin: tuple[float, float], dest: tuple[float, float], mode: str) -> float | None:
    key = _route_key(origin, dest, mode)
    row = session.get(RouteCache, key)
    if row is not None:
        return row.minutes
    res = ors_minutes(origin, [dest], mode)
    if res is None:
        return None  # API unavailable: don't cache, fall back to the estimate
    session.merge(RouteCache(key=key, minutes=res[0]))
    return res[0]


def commutes_for(session: Session, lat: float, lng: float, people_places: list, use_api: bool = False) -> list[Commute]:
    out: list[Commute] = []
    for person, place in people_places:
        coords = resolve_place(session, place)
        if coords is None:
            continue
        km = haversine_km(lat, lng, coords[0], coords[1])
        minutes, estimated = estimate_minutes(km, place.mode), True
        if use_api:
            api = cached_route_minutes(session, (lat, lng), coords, place.mode)
            if api is not None:
                minutes, estimated = api, False
        out.append(Commute(person.name, place.label, place.mode, round(km, 2), minutes,
                           place.max_minutes, place.hard_limit, estimated, place.weight * max(place.days_per_week, 1) / 5))
    return out
