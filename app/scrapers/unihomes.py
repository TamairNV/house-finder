"""UniHomes (unihomes.co.uk): all-bills-included student houses, priced per person per week.

Verified 2026-10-22 from TNV-server: listings are a Vue prop on the page,
:initial-properties="JSON.parse('...')" (HTML-entity + JS-string escaped JSON).
?page=N paginates (18-32 per page). Bedroom pages like
/student-accommodation/newcastle/6-bedroom-student-houses exist (4 untested).
"""
from __future__ import annotations

import html as htmllib
import json
import re

from ..profile import Profile, SourceLocation
from ..textparse import parse_available
from .base import RawListing
from .extract import to_float, to_int
from .generic import CardScraper

BASE = "https://www.unihomes.co.uk"
PROP_RE = re.compile(r":initial-properties=\"JSON\.parse\('(.*?)'\)\"", re.S)


def initial_properties(html: str) -> list[dict] | None:
    m = PROP_RE.search(html)
    if not m:
        return None
    raw = htmllib.unescape(m.group(1))
    try:
        decoded = json.loads('"' + raw.replace("\\'", "'") + '"')
        data = json.loads(decoded)
    except json.JSONDecodeError:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        data = data.get("data") or list(data.values())
    return data if isinstance(data, list) else None


class UniHomesScraper(CardScraper):
    name = "unihomes"
    label = "UniHomes"
    homepage = BASE
    base_url = BASE
    link_re = r"/property/(\d+)/"
    weekly_is_per_person = True

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        q = f"?page={page + 1}" if page else ""
        return f"{BASE}/student-accommodation/{loc.unihomes}{q}"

    def search(self, profile, loc, max_pages):
        """Try the bedroom-specific pages first (far fewer requests); fall back to the city page."""
        if not self.location_key(loc):
            return
        seen: set[str] = set()
        for beds in profile.allowed_bedrooms:
            base = f"{BASE}/student-accommodation/{loc.unihomes}/{beds}-bedroom-student-houses"
            for page in range(max_pages):
                url = base + (f"?page={page + 1}" if page else "")
                try:
                    html = self.fetcher.get(url)
                except FileNotFoundError:
                    break
                items = [i for i in self.parse_search(html, url) if i.source_id not in seen]
                if not items:
                    break
                for it in items:
                    seen.add(it.source_id)
                    yield it
                if ':initial-properties-has-more-pages="false"' in html:
                    break
        if not seen:
            yield from super().search(profile, loc, max_pages)

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        props = initial_properties(html)
        if props is None:
            return super().parse_search(html, url)
        return [self._from_json(p) for p in props if isinstance(p, dict) and p.get("id")]

    def _from_json(self, p: dict) -> RawListing:
        lid = str(p["id"])
        url = p.get("path") or f"{BASE}/property/{lid}"
        r = RawListing(source=self.name, source_id=lid, url=url)
        r.title = p.get("propertyName")
        r.address = ", ".join(x for x in [p.get("street"), p.get("addressShort"), p.get("postcode")] if x) or None
        r.postcode = p.get("postcode")
        m = re.search(r"/property/\d+/[^/]+/([^/]+)/", url)
        if m:
            r.area = m.group(1).replace("-", " ").title()
        r.lat, r.lng = to_float(p.get("latitude")), to_float(p.get("longitude"))
        r.bedrooms = to_int(p.get("bedroomCount"))
        r.bathrooms = to_float(p.get("bathroomCount")) or None
        r.property_type = p.get("type")
        price = to_float(p.get("price") or p.get("cheapestRoomPrice"))
        if price:
            r.price_amount, r.price_period, r.price_per_person = price, "pw", True
        r.bills_included = True  # UniHomes' whole model is bills-inclusive
        r.available_from, r.available_now = parse_available(str(p.get("availableFrom") or ""), self.today)
        imgs = [i.get("fileURLTablet") for i in (p.get("images") or []) if isinstance(i, dict) and i.get("fileURLTablet")]
        r.photos = imgs or ([p["mainImage"]] if p.get("mainImage") else [])
        r.description = p.get("shortDescription")
        r.student_let = True
        r.card_text = f"£{price} per person per week bills included {p.get('availableFrom') or ''}"
        r.raw = {"rooms_available": p.get("roomsAvailableCount"), "advert_type": p.get("advertType")}
        return r
