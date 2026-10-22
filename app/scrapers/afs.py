"""Accommodation for Students (accommodationforstudents.com).

Verified 2026-10-22 from TNV-server:
- Search: /{city}/student-houses/{n}-bedroom-student-houses?page=N (robots disallows /search-results*).
  __NEXT_DATA__ -> props.pageProps.viewModel.properties.groups[*].results[*].property
  (rent is terms.rentPpw.value, per person per week; contracts[0].startDate = tenancy start).
- Detail: /property/{id}-slug, __NEXT_DATA__ -> props.pageProps.viewModel.propertyDetails
  (bills.billsPackageQuote, amenities often hold "Compulsory £28PPPW bills package").
"""
from __future__ import annotations

import html as htmllib
import re

from ..profile import Profile, SourceLocation
from ..textparse import clean, parse_date_fragment
from .base import RawListing
from .extract import dget, first, next_data, soupify, to_float, to_int
from .generic import CardScraper

BASE = "https://www.accommodationforstudents.com"


class AFSScraper(CardScraper):
    name = "afs"
    label = "Accommodation for Students"
    homepage = BASE
    base_url = BASE
    link_re = r"/property/(\d+)-"
    weekly_is_per_person = True

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        n = page // len(self._beds(p))
        beds = self._beds(p)[page % len(self._beds(p))]
        q = f"?page={n + 1}" if n else ""
        return f"{BASE}/{loc.afs}/student-houses/{beds}-bedroom-student-houses{q}"

    @staticmethod
    def _beds(p: Profile) -> list[int]:
        return list(p.allowed_bedrooms)

    def search(self, profile, loc, max_pages):
        # Iterate bedroom pages separately so "no new results" on one size doesn't stop the others.
        if not self.location_key(loc):
            return
        seen: set[str] = set()
        for beds in profile.allowed_bedrooms:
            for page in range(max_pages):
                q = f"?page={page + 1}" if page else ""
                url = f"{BASE}/{loc.afs}/student-houses/{beds}-bedroom-student-houses{q}"
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
                pages = dget(next_data(soupify(html)) or {}, "props.pageProps.viewModel.pageCount")
                if pages and page + 1 >= int(pages):
                    break

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        data = next_data(soupify(html))
        groups = dget(data, "props.pageProps.viewModel.properties.groups") if data else None
        out: list[RawListing] = []
        for g in groups or []:
            for res in g.get("results") or []:
                p = res.get("property") or res
                if p.get("id") is None:
                    continue
                out.append(self._from_property(p))
        return out or super().parse_search(html, url)

    def _from_property(self, p: dict) -> RawListing:
        lid = str(p["id"])
        url = p.get("url") or f"/property/{lid}"
        r = RawListing(source=self.name, source_id=lid, url=BASE + url if url.startswith("/") else url)
        addr = p.get("address") or {}
        r.address = ", ".join(x for x in [addr.get("address1"), addr.get("address2"), addr.get("area"), addr.get("postcode")] if x) or None
        r.area = addr.get("area")
        r.postcode = addr.get("postcode")
        r.lat = to_float(first(p, "coordinates.lat", "coordinates.latitude"))
        r.lng = to_float(first(p, "coordinates.lon", "coordinates.lng", "coordinates.longitude"))
        r.bedrooms = to_int(first(p, "occupancy.total"))
        r.bathrooms = to_float(p.get("numberOfBathrooms")) or None
        r.property_type = p.get("propertyType")
        rent = to_float(first(p, "terms.rentPpw.value", "terms.currentMarketingYearRentPpw.value"))
        if rent:
            r.price_amount, r.price_period, r.price_per_person = rent, "pw", True
        bills = str(first(p, "terms.billsIncluded") or "").lower()
        r.bills_included = True if bills in ("all", "allbills", "yes", "true") else (False if bills in ("none", "no") else None)
        start = first(p, "contracts.0.startDate")
        if start:
            r.available_from = parse_date_fragment(str(start)[:10], self.today)
        r.photos = [i.get("url") for i in (p.get("images") or []) if isinstance(i, dict) and i.get("url")]
        r.agent_name = first(p, "accommodationProvider.name")
        r.student_let = True
        r.let_agreed = bool(p.get("isSoldOut"))
        r.title = f"{r.bedrooms} bed {r.property_type or 'house'}" + (f", {addr.get('address1')}" if addr.get("address1") else "")
        r.card_text = clean(f"{p.get('academicYearLabel') or ''} £{rent} pppw bills {bills}")
        return r

    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        data = next_data(soupify(html))
        d = dget(data, "props.pageProps.viewModel.propertyDetails") if data else None
        if not d:
            return super().parse_detail(html, base)
        r = RawListing(source=self.name, source_id=base.source_id, url=base.url)
        r.description = clean(htmllib.unescape(re.sub(r"<[^>]+>", " ", d.get("description") or "")))
        amen = [a.get("name") for a in (d.get("amenities") or []) if isinstance(a, dict) and a.get("name")]
        r.key_features = [clean(a) for a in amen]
        r.bathrooms = to_float(d.get("numberOfBathrooms")) or None
        r.bedrooms = to_int(first(d, "occupancy.total"))
        av = d.get("availability") or {}
        if av.get("availableNow"):
            r.available_from, r.available_now = self.today, True
        elif av.get("availableFromDate"):
            r.available_from = parse_date_fragment(av["availableFromDate"], self.today)
        bills = d.get("bills") or {}
        which = str(bills.get("whichBillsAreIncluded") or "").lower()
        r.bills_included = True if which == "all" else (False if which in ("none", "some") else None)
        quote = bills.get("billsPackageQuote") or {}
        if quote.get("totalBillsPerWeek"):
            r.raw = {"bills_package_pppw": to_float(quote["totalBillsPerWeek"])}
            r.card_text = f"bills package £{quote['totalBillsPerWeek']} pppw"
        rent = to_float(first(d, "price.rentPerWeekFrom"))
        if rent:
            r.price_amount, r.price_period, r.price_per_person = rent, "pw", True
        r.deposit = to_float(first(d, "price.deposit"))
        r.photos = [i.get("url") for i in (d.get("images") or []) if isinstance(i, dict) and i.get("url")]
        r.lat = to_float(first(d, "coordinates.latitude"))
        r.lng = to_float(first(d, "coordinates.longitude"))
        r.postcode = first(d, "address.postcode")
        r.agent_name = first(d, "accommodationProvider.companyName")
        r.agent_phone = first(d, "accommodationProvider.phoneNumber")
        return base.merge(r)
