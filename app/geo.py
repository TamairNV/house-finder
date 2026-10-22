"""Geocoding (postcodes.io, free, no key) and commute-time estimates."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import GeoCache
from .profile import Place

log = logging.getLogger(__name__)

POSTCODES_IO = "https://api.postcodes.io"

# Rough door-to-door speeds used when no routing API is configured.
# detour = road distance / straight-line distance, typical for UK cities.
SPEEDS_KMH = {"walk": 4.8, "cycle": 15.0, "transit": 16.0, "drive": 22.0}
DETOUR = {"walk": 1.3, "cycle": 1.25, "transit": 1.35, "drive": 1.4}
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


def ors_minutes(origin: tuple[float, float], dests: list[tuple[float, float]], mode: str) -> list[float | None] | None:
    """openrouteservice matrix call (free key, 500 matrix requests/day)."""
    key = get_settings().ors_api_key
    if not key or mode not in ORS_PROFILES or not dests:
        return None
    body = {"locations": [[origin[1], origin[0]]] + [[d[1], d[0]] for d in dests],
            "sources": [0], "destinations": list(range(1, len(dests) + 1)), "metrics": ["duration"]}
    try:
        r = httpx.post(f"https://api.openrouteservice.org/v2/matrix/{ORS_PROFILES[mode]}",
                       json=body, headers={"Authorization": key}, timeout=30)
        r.raise_for_status()
        durs = r.json()["durations"][0]
        return [round(d / 60, 1) if d is not None else None for d in durs]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("ORS matrix failed: %s", e)
        return None


def commutes_for(session: Session, lat: float, lng: float, people_places: list, use_api: bool = False) -> list[Commute]:
    out: list[Commute] = []
    for person, place in people_places:
        coords = resolve_place(session, place)
        if coords is None:
            continue
        km = haversine_km(lat, lng, coords[0], coords[1])
        minutes, estimated = estimate_minutes(km, place.mode), True
        if use_api:
            api = ors_minutes((lat, lng), [coords], place.mode)
            if api and api[0] is not None:
                minutes, estimated = api[0], False
        out.append(Commute(person.name, place.label, place.mode, round(km, 2), minutes,
                           place.max_minutes, place.hard_limit, estimated, place.weight * max(place.days_per_week, 1) / 5))
    return out
