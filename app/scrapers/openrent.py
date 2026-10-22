"""OpenRent (openrent.co.uk): landlord-direct lets, no agency fees.

Verified 2026-10-22 from TNV-server:
- Search: /properties-to-rent/{area}?term=... ships EVERY result in the area (~250) as parallel
  JS arrays (PROPERTYIDS, prices, bedrooms, bathrooms, isshared, students, bills, availableFrom
  (days from today), PROPERTYLISTLATITUDES...). Bedroom filters in the URL are ignored, so we
  filter here. Some arrays are one shorter than PROPERTYIDS; only use arrays whose length matches.
- Detail: https://www.openrent.co.uk/{id} redirects to the canonical page. Facts in
  <table> rows (<td class="fw-medium">Label</td><td>value</td>); booleans are svg icons with
  class text-success / text-danger; coords in data-lat / data-lng; description in #descriptionText.
"""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import quote

from ..profile import Profile, SourceLocation
from ..textparse import clean
from .base import RawListing
from .extract import cards_by_link, js_arrays, soupify, text_of, to_float, to_int
from .generic import CardScraper

BASE = "https://www.openrent.co.uk"


class OpenRentScraper(CardScraper):
    name = "openrent"
    label = "OpenRent"
    homepage = BASE
    base_url = BASE
    link_re = r"/property-to-rent/[^/]+/[^/]+/(\d+)"
    photo_host = "imagescdn.openrent.co.uk/listings"

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        if page > 0:
            return None  # the arrays already contain every listing in the area
        return f"{BASE}/properties-to-rent/{loc.openrent}?term={quote(loc.label)}"

    def search(self, profile, loc, max_pages):
        self._allowed = set(profile.allowed_bedrooms)
        yield from super().search(profile, loc, 1)

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        arrays = js_arrays(html)
        ids = [str(x) for x in arrays.get("PROPERTYIDS", [])]
        if not ids:
            return super().parse_search(html, url)
        n = len(ids)
        col = {k: v for k, v in arrays.items() if len(v) == n}

        def get(name: str, i: int):
            return col[name][i] if name in col else None

        # Titles for the ~20 server-rendered cards
        titles = {lid: text_of(card).split("£")[0].strip() for lid, _, card in cards_by_link(soupify(html), self.link_re, BASE)}
        allowed = getattr(self, "_allowed", None)
        out = []
        for i, lid in enumerate(ids):
            beds = to_int(get("bedrooms", i))
            if get("isshared", i) == 1 or get("isstudio", i) == 1 or get("islivelistBool", i) == 0:
                continue
            if allowed and beds is not None and beds not in allowed:
                continue
            r = RawListing(source=self.name, source_id=lid, url=f"{BASE}/{lid}")
            r.bedrooms = beds
            r.bathrooms = to_float(get("bathrooms", i)) or None
            price = to_float(get("prices", i))
            if price:
                r.price_amount, r.price_period = price, "pcm"
            r.lat = to_float(get("PROPERTYLISTLATITUDES", i))
            r.lng = to_float(get("PROPERTYLISTLONGITUDES", i))
            bills = get("bills", i)
            r.bills_included = None if bills is None else bool(bills)
            days = get("availableFrom", i)
            if isinstance(days, (int, float)):
                r.available_from = self.today + timedelta(days=int(days))
                r.available_now = days <= 0
            mt = get("minimumTenancy", i)
            r.min_term_months = int(mt) if isinstance(mt, (int, float)) and mt > 0 else None
            st = get("students", i)
            r.student_let = None if st is None else bool(st)
            fu, un = get("furnished", i), get("unfurnished", i)
            r.furnished = "furnished" if fu == 1 else ("unfurnished" if un == 1 else None)
            title = titles.get(lid)
            if title:
                r.title = title[:150]
                r.address = ", ".join(title.split(",")[1:]).strip() or None
            r.card_text = clean(f"{title or ''} £{price} pcm")
            out.append(r)
        return out

    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        r = super().parse_detail(html, base)
        soup = soupify(html)
        h1 = soup.find("h1")
        if h1:
            r.title = text_of(h1)
            r.address = ", ".join(r.title.split(",")[1:]).strip() or r.address
        desc = soup.find(id="descriptionText")
        if desc:
            r.description = text_of(desc)
        page = text_of(soup)
        m = re.search(r"(\d+)\s+tenants?\s+max", page, re.I)
        if m:
            r.raw = {**r.raw, "max_tenants": int(m.group(1))}
        m = re.search(r"(\d+)\s*bathrooms?\b", page, re.I)
        if m:
            r.bathrooms = float(m.group(1))
        # Detail-page date beats the relative-days value from the search arrays
        return r
