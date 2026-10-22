"""Shared logic for sites where we read listing cards as text + detail label/value pairs."""
from __future__ import annotations

import re

from ..textparse import (clean, detect_bills_included, parse_available, parse_bathrooms, parse_bedrooms,
                         parse_date_fragment, parse_furnished, parse_money, parse_postcode)
from .base import RawListing, Scraper
from .extract import (card_images, cards_by_link, find_latlng, image_urls, json_ld, label_values, meta_content,
                      soupify, text_of, yes_no)


class CardScraper(Scraper):
    """Subclasses set ``link_re`` (with one capture group = listing id) and ``base_url``."""

    link_re: str = ""
    base_url: str = ""
    photo_host: str | None = None  # substring that identifies listing photos on this site

    def card_to_raw(self, lid: str, url: str, text: str, card) -> RawListing:
        r = RawListing(source=self.name, source_id=lid, url=url, card_text=text)
        r.bedrooms = parse_bedrooms(text)
        r.bathrooms = parse_bathrooms(text)
        r.bills_included = detect_bills_included(text)
        r.available_from, r.available_now = parse_available(text, self.today)
        r.photos = [u for u in card_images(card, self.base_url) if not self.photo_host or self.photo_host in u]
        r.let_agreed = bool(re.search(r"\blet agreed\b|\bunder offer\b", text, re.I))
        heading = card.find(["h2", "h3", "h4"])
        r.title = text_of(heading) if heading else None
        addr = card.find("address")
        if addr:
            r.address = text_of(addr)
        if self.weekly_is_per_person:
            m = re.search(r"£\s?([\d,]+(?:\.\d+)?)\s*(?:pppw|pp\s?pw|per person per week|/\s?week|pw|per week|a week)", text, re.I)
            if m:
                r.price_amount, r.price_period, r.price_per_person = parse_money(m.group(1)), "pw", True
        return r

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        soup = soupify(html)
        out = []
        for lid, href, card in cards_by_link(soup, self.link_re, self.base_url):
            text = text_of(card)
            if "£" not in text:
                continue  # navigation link, not a card
            out.append(self.card_to_raw(lid, href, text, card))
        return out

    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        soup = soupify(html)
        r = RawListing(source=self.name, source_id=base.source_id, url=base.url)
        pairs = label_values(soup)
        main = soup.find("main") or soup.body or soup
        page_text = text_of(main)
        r.card_text = page_text[:8000]
        r.raw = {"labels": dict(list(pairs.items())[:80])}

        def pv(*keys: str) -> str | None:
            for k in keys:
                for label, val in pairs.items():
                    if label == k or label.startswith(k):
                        return val
            return None

        avail = pv("available from", "available", "move in", "tenancy start", "let available date")
        if avail:
            if re.match(r"\s*(now|immediately|today)", avail, re.I):
                r.available_from, r.available_now = self.today, True
            else:
                r.available_from = parse_date_fragment(avail, self.today)
        if r.available_from is None:
            r.available_from, r.available_now = parse_available(page_text, self.today)
        bills = pv("bills included", "bills")
        r.bills_included = yes_no(bills) if bills else detect_bills_included(page_text)
        baths = pv("bathrooms", "bathroom")
        r.bathrooms = float(parse_money(baths)) if baths and parse_money(baths) is not None else parse_bathrooms(page_text)
        beds = pv("bedrooms", "bedroom")
        r.bedrooms = int(parse_money(beds)) if beds and parse_money(beds) is not None else parse_bedrooms(page_text)
        r.deposit = parse_money(pv("deposit"))
        r.furnished = parse_furnished(pv("furnishing", "furnished", "furnish type") or "")
        epc = pv("epc rating", "epc")
        if epc and re.match(r"^[A-G]\b", epc.strip().upper()):
            r.epc = epc.strip().upper()[0]
        term = pv("minimum tenancy", "minimum term", "min term", "tenancy length")
        if term and parse_money(term):
            n = int(parse_money(term))
            r.min_term_months = n if "month" in term.lower() else (round(n / 4.33) if "week" in term.lower() else n)
        ll = find_latlng(html)
        if ll:
            r.lat, r.lng = ll
        for item in json_ld(soup):
            if isinstance(item, dict):
                geo = item.get("geo") or {}
                if geo.get("latitude") and r.lat is None:
                    r.lat, r.lng = float(geo["latitude"]), float(geo["longitude"])
                addr = item.get("address")
                if isinstance(addr, dict) and addr.get("postalCode"):
                    r.postcode = addr["postalCode"]
        r.photos = [u for u in image_urls(html, self.photo_host) if not re.search(r"logo|icon|avatar|static_?map|staticmap|placeholder", u, re.I)]
        og = meta_content(soup, "og:image")
        if og and og not in r.photos and (not self.photo_host or self.photo_host in og):
            r.photos.insert(0, og)
        desc_el = soup.find(attrs={"class": re.compile(r"description", re.I)}) or soup.find(id=re.compile(r"description", re.I))
        r.description = text_of(desc_el)[:6000] if desc_el else (meta_content(soup, "og:description") or None)
        title = meta_content(soup, "og:title") or (text_of(soup.find("h1")) if soup.find("h1") else None)
        r.title = clean(title) if title else None
        if not r.postcode:
            pc, _ = parse_postcode(page_text[:4000])
            r.postcode = pc
        r.let_agreed = bool(re.search(r"\blet agreed\b|\bno longer available\b|\bthis property has been let\b", page_text[:5000], re.I))
        return base.merge(r)
