"""Zoopla (zoopla.co.uk). Behind Cloudflare: usually needs HF_PLAYWRIGHT_ENABLED=true.

Search: /to-rent/property/{area}/?beds_min=&beds_max=&price_frequency=per_month&price_max=&results_sort=newest_listings&pn=N
Detail: /to-rent/details/{id}/
"""
from __future__ import annotations

from ..profile import Profile, SourceLocation
from ..textparse import parse_money
from .base import RawListing
from .extract import dget, find_dicts, first, image_urls, next_data, soupify, to_float, to_int
from .generic import CardScraper

BASE = "https://www.zoopla.co.uk"


class ZooplaScraper(CardScraper):
    name = "zoopla"
    label = "Zoopla"
    homepage = BASE
    base_url = BASE
    link_re = r"/to-rent/details/(\d+)"
    photo_host = "zoocdn.com"
    uses_browser = True

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        beds_min, beds_max = p.group_size, p.group_size + p.extra_bedrooms_allowed
        return (f"{BASE}/to-rent/property/{loc.zoopla}/?beds_min={beds_min}&beds_max={beds_max}"
                f"&price_frequency=per_month&price_max={self.max_rent_pcm(p)}&results_sort=newest_listings&pn={page + 1}")

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        soup = soupify(html)
        data = next_data(soup)
        out: list[RawListing] = []
        if data:
            for d in find_dicts(data, "listingId", any_of=("price", "address", "title")):
                lid = str(d["listingId"])
                r = RawListing(source=self.name, source_id=lid, url=f"{BASE}/to-rent/details/{lid}/")
                r.title = d.get("title")
                r.address = d.get("address")
                r.lat = to_float(first(d, "pos.lat", "location.coordinates.latitude"))
                r.lng = to_float(first(d, "pos.lng", "location.coordinates.longitude"))
                price = first(d, "price", "pricing.label")
                if isinstance(price, str):
                    r.price_amount = parse_money(price)
                    r.price_period = "pw" if "pw" in price.lower() and "pcm" not in price.lower() else "pcm"
                for f in d.get("features") or []:
                    if isinstance(f, dict):
                        if f.get("iconId") == "bed":
                            r.bedrooms = to_int(f.get("content"))
                        elif f.get("iconId") == "bath":
                            r.bathrooms = to_float(f.get("content"))
                r.photos = image_urls(d, "zoocdn.com")
                r.card_text = " ".join(str(x) for x in [d.get("summaryDescription"), d.get("title"), price,
                                                          dget(d, "availableFrom"), d.get("tags")] if x)
                out.append(r)
        return out or super().parse_search(html, url)
