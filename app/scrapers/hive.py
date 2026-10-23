"""Hive Estates (hiveestates.com): Newcastle letting agent (Jesmond/Heaton/Fenham, students and professionals).

Verified 2026-10-23 from TNV-server:
- robots.txt only blocks /wp-admin/. WordPress site fed from Alto (photos on alto-live.s3.amazonaws.com).
- /properties/page/{n}/ lists every let (12 per page, ~5 pages); the "Student" search filter is a POST that
  doesn't actually narrow anything, so we walk the plain pages and let scoring filter on bedrooms/price.
- Cards: div.mw-prop-box > a.mw-pb-title, p.mw-pb-price ("£1,525 pcm" whole property), ul.mw-pb-list
  ("3 bedrooms", "1 bathroom"), h4.mw-spc-flag ("Let Agreed").
- Detail: h1 address, ul.mw-sph-details, ul.mw-spc-feat-list, ul.mw-spc-availability ("Available from: November
  2026"), div.mw-spc-desc, `var mapInfo = [...{"lat":"54.98","lng":"-1.57"}]`, photos in a.slick-expand.
"""
from __future__ import annotations

import json
import re

from ..profile import Profile, SourceLocation
from ..textparse import clean, parse_bathrooms, parse_bedrooms, parse_date_fragment, parse_money
from .base import RawListing, Scraper
from .extract import soupify, text_of, to_float

BASE = "https://hiveestates.com"
DETAIL_RE = re.compile(r"/property/property-(\d+)-")


class HiveScraper(Scraper):
    name = "hive"
    label = "Hive Estates"
    homepage = BASE

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        return f"{BASE}/properties/" if page == 0 else f"{BASE}/properties/page/{page + 1}/"

    def parse_search(self, html: str, url: str) -> list[RawListing]:
        out = []
        for box in soupify(html).select("div.mw-prop-box"):
            a = box.select_one("a.mw-pb-title")
            m = DETAIL_RE.search(a.get("href", "")) if a else None
            if not m:
                continue
            r = RawListing(source=self.name, source_id=m.group(1), url=a["href"])
            r.title = r.address = clean(text_of(a))
            self._price(r, text_of(box.select_one(".mw-pb-price")))
            facts = " ".join(text_of(li) for li in box.select(".mw-pb-list li"))
            r.bedrooms, r.bathrooms = parse_bedrooms(facts), parse_bathrooms(facts)
            flag = text_of(box.select_one(".mw-spc-flag")).lower()
            r.let_agreed = "let agreed" in flag or "let" == flag.strip()
            img = box.select_one("a.dpj-aspect-img[style]")
            if img and (u := re.search(r"url\(([^)]+)\)", img["style"])):
                r.photos = [u.group(1).strip("'\"")]
            r.agent_name = self.label
            r.card_text = clean(f"{r.title} {facts}")
            out.append(r)
        return out

    @staticmethod
    def _price(r: RawListing, text: str) -> None:
        amount = parse_money(text)
        if not amount:
            return
        low = text.lower()
        r.price_amount = amount
        r.price_period = "pw" if ("pw" in low or "week" in low) else "pcm"
        r.price_per_person = "pppw" in low or "per person" in low

    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        soup = soupify(html)
        r = base
        if (h1 := soup.select_one(".mw-s-prop-header h1")):
            r.title = r.address = clean(text_of(h1))
        details = [text_of(li) for li in soup.select(".mw-sph-details li")]
        for d in details:
            if "£" in d:
                self._price(r, d)
        joined = " ".join(details)
        r.bedrooms = parse_bedrooms(joined) or r.bedrooms
        r.bathrooms = parse_bathrooms(joined) or r.bathrooms
        r.key_features = [clean(text_of(li)) for li in soup.select(".mw-spc-feat-list li") if text_of(li)]
        desc = soup.select_one(".mw-spc-main .mw-spc-desc") or soup.select_one(".mw-spc-desc")
        if desc:
            for h in desc.select("h3"):
                h.decompose()
            r.description = clean(text_of(desc))
        for li in soup.select(".mw-spc-availability li"):
            label, _, value = text_of(li).partition(":")
            if label.strip().lower().startswith("available") and value.strip():
                if "now" in value.lower() or "immediately" in value.lower():
                    r.available_now = True
                else:
                    r.available_from = parse_date_fragment(value, self.today) or r.available_from
        m = re.search(r"var mapInfo\s*=\s*(\[.*?\]);", html, re.S)
        if m:
            try:
                pins = [p for p in json.loads(m.group(1)) if isinstance(p, dict) and p.get("lat")]
                if pins:
                    r.lat, r.lng = to_float(pins[0]["lat"]), to_float(pins[0]["lng"])
            except (json.JSONDecodeError, KeyError):
                pass
        photos = [a["href"] for a in soup.select("a.slick-expand[href]") if "alto-live" in a["href"]]
        if photos:
            r.photos = list(dict.fromkeys(photos))
        if soup.select_one(".mw-spc-flag"):
            r.let_agreed = "let agreed" in text_of(soup.select_one(".mw-spc-flag")).lower() or r.let_agreed
        r.agent_name = self.label
        text = f"{r.description or ''} {' '.join(r.key_features)}".lower()
        if "student" in text:
            r.student_let = True
        return r
