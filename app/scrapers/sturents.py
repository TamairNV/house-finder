"""StuRents (sturents.com): student houses, prices per person per week.

Verified 2026-10-22 from TNV-server:
- robots.txt: Disallow / but Allow /student-accommodation, /house, /listings, /sitemap ...
- City landing page server-renders only 8 cards (a.js-listing-item with a data-house JSON attr).
  The full search (/s/...) is filled client-side; its XHR endpoint is still unknown (TODO in PLAN.md).
  To see everything we also walk the XML sitemap for /student-accommodation/{city}/house/ URLs.
- Detail: application/ld+json @type House (bedrooms, bathrooms, geo, postalCode) and
  p.test-availability-* lines ("Move in from: 01 Aug 2027", "Rent: £158 pppw (£948 property/week)",
  "Bills included: There are no bills included"); photos in og:image / public.sturents.com/photos.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import re

from ..profile import Profile, SourceLocation
from ..textparse import clean, detect_bills_included, parse_bedrooms, parse_date_fragment, parse_money
from .base import RawListing
from .extract import image_urls, json_ld, soupify, text_of, to_float, to_int
from .generic import CardScraper

log = logging.getLogger(__name__)
BASE = "https://sturents.com"
DETAIL_RE = r"/student-accommodation/[^/]+/(?:house|flat|property)/[^/]+/(\d+)"


class StuRentsScraper(CardScraper):
    name = "sturents"
    label = "StuRents"
    homepage = BASE
    base_url = BASE
    link_re = DETAIL_RE
    weekly_is_per_person = True
    photo_host = "sturents.com/photos"

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        return f"{BASE}/student-accommodation/{loc.sturents}" if page == 0 else None

    def search(self, profile, loc, max_pages):
        if not self.location_key(loc):
            return
        seen: set[str] = set()
        html = self.fetcher.get(self.search_url(profile, loc, 0))
        for r in self.parse_search(html, BASE):
            seen.add(r.source_id)
            yield r
        # Sitemap walk for the rest (only ids/urls; details fill in the rest)
        try:
            for url in self._sitemap_urls(loc.sturents or "", limit_maps=max_pages):
                m = re.search(DETAIL_RE, url)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    yield RawListing(source=self.name, source_id=m.group(1), url=url)
        except Exception as e:  # noqa: BLE001  sitemap is a bonus, not essential
            log.warning("[sturents] sitemap walk failed: %s", e)

    def _sitemap_urls(self, city: str, limit_maps: int) -> list[str]:
        index = self.fetcher.get(f"{BASE}/sitemap/index.xml")
        maps = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", index)
        # Prefer sub-sitemaps that mention houses/accommodation
        maps.sort(key=lambda u: 0 if re.search(r"house|accommodation|listing", u) else 1)
        urls: list[str] = []
        for sm in maps[: max(limit_maps, 1)]:
            body = self.fetcher.get(sm)
            urls += [u for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", body) if f"/student-accommodation/{city}" in u]
        return urls

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        soup = soupify(html)
        out = []
        for a in soup.select("a[data-house]"):
            try:
                d = json.loads(htmllib.unescape(a["data-house"]))
            except (json.JSONDecodeError, KeyError):
                continue
            if not isinstance(d, dict) or not d:
                continue  # empty data-house="[]" on template/placeholder anchors
            href = a.get("href") or d.get("url") or ""
            m = re.search(DETAIL_RE, href)
            lid = str(d.get("house_id") or (m.group(1) if m else ""))
            if not lid:
                continue
            r = RawListing(source=self.name, source_id=lid, url=href if href.startswith("http") else BASE + href)
            r.title = d.get("title")
            r.address = d.get("meta_address") or d.get("title")
            r.bedrooms = parse_bedrooms(str(d.get("beds") or ""))
            r.lat, r.lng = to_float(d.get("lat")), to_float(d.get("lng"))
            price = to_float(d.get("price"))
            if price:
                r.price_amount, r.price_period, r.price_per_person = price, "pw", True
            if d.get("from"):
                r.available_from = parse_date_fragment(str(d["from"]), self.today)
            bi = str(d.get("bills_included") or "").lower()
            r.bills_included = True if bi == "yes" else (False if bi == "no" else None)
            r.description = clean(d.get("description"))
            r.photos = [u for u in (d.get("photo_urls") or []) if u] or ([d["photo"]] if d.get("photo") else [])
            r.photos = [u.replace("/photo-thumbs/", "/photos/") for u in r.photos]
            r.agent_name = d.get("landlord")
            r.student_let = True
            r.card_text = clean(f"{d.get('beds')} {d.get('price_string') or ''} {d.get('rental_type') or ''}")
            if d.get("rental_type") and "whole" not in str(d["rental_type"]).lower():
                r.raw = {"rental_type": d["rental_type"]}
            out.append(r)
        return out or super().parse_search(html, url)

    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        r = super().parse_detail(html, base)
        soup = soupify(html)
        for item in json_ld(soup):
            if isinstance(item, dict) and item.get("@type") in ("House", "Apartment", "Accommodation", "SingleFamilyResidence"):
                r.bedrooms = to_int(item.get("numberOfBedrooms")) or r.bedrooms
                r.bathrooms = to_float(item.get("numberOfBathroomsTotal")) or r.bathrooms
                r.title = item.get("name") or r.title
                addr = item.get("address")
                addr = addr[0] if isinstance(addr, list) and addr else addr
                if isinstance(addr, dict):
                    r.postcode = addr.get("postalCode") or r.postcode
                    r.address = ", ".join(x for x in [addr.get("streetAddress"), addr.get("addressRegion"), addr.get("postalCode")] if x)
                geo = item.get("geo") or {}
                if geo.get("latitude"):
                    r.lat, r.lng = float(geo["latitude"]), float(geo["longitude"])
                feats = [f.get("@name") or f.get("name") for f in (item.get("amenityFeature") or []) if isinstance(f, dict)]
                r.key_features = [f for f in feats if f]
        def cls(name: str) -> str:
            el = soup.select_one(f".test-availability-{name}")
            return text_of(el) if el else ""
        frm = cls("date-from")
        if frm:
            r.available_from = parse_date_fragment(frm.split(":", 1)[-1], self.today)
        rent = cls("rent")
        m = re.search(r"£\s?([\d,.]+)\s*pppw", rent, re.I)
        if m:
            r.price_amount, r.price_period, r.price_per_person = parse_money(m.group(1)), "pw", True
        bills = cls("bills-included")
        if bills:
            low = bills.lower()
            r.bills_included = False if ("no bills" in low or "not included" in low) else (
                True if "all bills" in low or "included" in low and "no " not in low else detect_bills_included(bills))
        # The generic label scan picks up "Deposit:" lines inside the description (e.g. a NOPOSIT fee);
        # the availability list has the real one: <strong>Deposit: </strong> £0 pp
        r.deposit = None
        for strong in soup.find_all("strong"):
            if text_of(strong).lower().startswith("deposit"):
                dep = parse_money(text_of(strong.parent))
                r.deposit = dep or None  # £0 means "not stated" more often than "no deposit"
                break
        photos = [u for u in image_urls(html, "public.sturents.com/photos")]
        if photos:
            r.photos = photos
        return r
