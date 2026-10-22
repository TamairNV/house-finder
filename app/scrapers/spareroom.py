"""SpareRoom (spareroom.co.uk). Mostly single rooms; we keep only whole-property ads.

Verified 2026-10-22 from TNV-server:
- Search: /flatshare/{area} and /flatshare/{area}/pageN (robots disallows most query filters).
  Cards are li.listing-result with data-listing-* attributes; whole-property ads say "N bed house".
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
            m = WHOLE_RE.search(text)
            if not m:
                continue  # room in a share, not useful for a group
            lid = li["data-listing-id"]
            link = li.select_one("a.listing-card__link") or li.find("a", href=True)
            href = link["href"].split("?")[0] if link else f"/flatshare/{lid}"
            r = RawListing(source=self.name, source_id=lid, url=BASE + href if href.startswith("/") else href, card_text=text)
            r.bedrooms = int(m.group(1))
            r.property_type = m.group(2).lower()
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
        return r
