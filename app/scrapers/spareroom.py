"""SpareRoom (spareroom.co.uk). Mostly single rooms; we keep only whole-property ads.

Verified 2026-10-22 from TNV-server:
- Search: /flatshare/{area} and /flatshare/{area}/pageN (robots disallows most query filters).
  Cards are li.listing-result with data-listing-* attributes. span.listing-card__room says what is
  offered: "3 bed flat" (whole property, total rent), "6 doubles" (several rooms in one house, rent
  per room) or "Double room" / "Single room" (one room: useless for a group, skipped).
- Detail: section.feature dl.feature-list dt/dd pairs ("Bills included?", "Available",
  "Minimum term"); coords in an inline JS object location: {latitude: "..", longitude: ".."}.
"""
from __future__ import annotations

import html as htmllib
import re

from ..profile import Profile, SourceLocation
from ..textparse import detect_bills_included, parse_available, parse_money
from .base import RawListing
from .extract import soupify, text_of
from .generic import CardScraper

BASE = "https://www.spareroom.co.uk"
WHOLE_RE = re.compile(r"\b(\d+)\s*bed(?:room)?s?\s+(house|flat|property|maisonette|bungalow|apartment)", re.I)
MULTI_RE = re.compile(r"^\s*(\d+)\s+(?:doubles?|singles?|rooms?|ensuites?|en-suites?)\b", re.I)


class SpareRoomScraper(CardScraper):
    name = "spareroom"
    label = "SpareRoom"
    homepage = BASE
    base_url = BASE
    link_re = r"/flatshare/[a-z_]+/[a-z_]+/(\d{6,})"
    photo_host = "photos2.spareroom.co.uk"

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        suffix = f"/page{page + 1}" if page else ""
        return f"{BASE}/flatshare/{loc.spareroom}{suffix}"

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        soup = soupify(html)
        out = []
        cards = soup.select("li.listing-result[data-listing-id]")
        if not cards:
            return [r for r in super().parse_search(html, url) if WHOLE_RE.search(r.card_text)]
        for li in cards:
            text = text_of(li)
            room_el = li.select_one(".listing-card__room")
            # Classify on the "what's offered" line only: descriptions of single-room ads often say
            # "one double room in this 5 bed house", which must not count as a whole property.
            offered = text_of(room_el) if room_el else text
            whole, multi = WHOLE_RE.search(offered), MULTI_RE.search(offered)
            if not whole and not (multi and int(multi.group(1)) >= 2):
                continue  # a single room in a share, not useful for a group
            lid = li["data-listing-id"]
            link = li.select_one("a.listing-card__link") or li.find("a", href=True)
            href = link["href"].split("?")[0] if link else f"/flatshare/{lid}"
            r = RawListing(source=self.name, source_id=lid, url=BASE + href if href.startswith("/") else href, card_text=text)
            if whole:
                r.bedrooms = int(whole.group(1))
                r.property_type = whole.group(2).lower()
            else:
                r.bedrooms = int(multi.group(1))  # rooms on offer, i.e. how many of us fit
                r.property_type = (li.get("data-listing-property-type") or "").lower() or None
                r.price_per_person = True
                r.raw = {"rooms_offered": r.bedrooms, "rooms_in_property": li.get("data-listing-rooms-in-property")}
            title = li.select_one(".listing-card__title")
            r.title = text_of(title) if title else None
            loc = li.select_one(".listing-card__location")
            r.address = text_of(loc) if loc else None
            r.postcode = None
            rate = htmllib.unescape(li.get("data-listing-ad-headline-rate") or "")
            period = (li.get("data-listing-ad-headline-rate-period") or "").lower()
            if rate:
                r.price_amount = parse_money(rate)
                r.price_period = "pw" if period.startswith("pw") or "week" in period else "pcm"
            r.bills_included = True if li.select_one(".listing-card__bills-included") else detect_bills_included(text)
            r.available_from, r.available_now = parse_available(text, self.today)
            if li.get("data-listing-available-now") == "1":
                r.available_from, r.available_now = self.today, True
            img = li.select_one("img.listing-card__main-image")
            if img and img.get("src"):
                src = img["src"].split("?")[0]
                r.photos = [("https:" + src if src.startswith("//") else src).replace("/unmodified/", "/large/")]
            out.append(r)
        return out

    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        r = super().parse_detail(html, base)
        soup = soupify(html)
        desc = soup.select_one(".detaildesc")
        if desc:
            r.description = text_of(desc)
        r.photos = list(dict.fromkeys(u.replace("/square/", "/large/").replace("/unmodified/", "/large/") for u in r.photos))
        # Per-room rents: <section class="feature--price_room_only"><dt>£750 pcm</dt><dd>double (En suite)</dd>
        rooms = []
        for dt in soup.select(".feature--price_room_only dt.feature-list__key"):
            m = re.search(r"£\s?([\d,.]+)\s*(pcm|pw)", text_of(dt), re.I)
            if m:
                rooms.append((parse_money(m.group(1)), m.group(2).lower()))
        if len(rooms) >= 2 or (rooms and r.price_per_person):
            amount, period = min(rooms)
            r.price_amount, r.price_period, r.price_per_person = amount, period, True
            r.raw = {**r.raw, "room_rents": [a for a, _ in rooms]}
        return r
