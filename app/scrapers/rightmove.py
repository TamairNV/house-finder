"""Rightmove (rightmove.co.uk). Biggest portal; most Newcastle student agents list here.

Verified against live pages from TNV-server on 2026-10-22:
- Search: /property-to-rent/find.html?locationIdentifier=REGION^984 (Newcastle upon Tyne).
  <script id="__NEXT_DATA__"> -> props.pageProps.searchResults.properties (24-25 per page),
  paginate with &index=24,48... Each property already has letAvailableDate (ISO).
- Detail: /properties/{id}. window.__PAGE_MODEL = {"data": "<json string>", "encoding": "on"},
  where data is a flattened, index-referenced array (devalue style), see decode_flat().
  propertyData has lettings, prices, images, floorplans, keyFeatures, text, location, address.
Robots allows both paths; Rightmove's T&Cs prohibit scraping, see PLAN.md (personal, low-rate use).
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from ..profile import Profile, SourceLocation
from ..textparse import clean, parse_available, parse_bathrooms, parse_bedrooms, parse_date_fragment, parse_furnished, parse_money, parse_postcode
from .base import RawListing, Scraper
from .extract import (card_images, cards_by_link, dget, find_dicts, first, image_urls, js_assignment, label_values,
                      next_data, soupify, text_of, to_float, to_int)

BASE = "https://www.rightmove.co.uk"
MEDIA = "https://media.rightmove.co.uk/"
PAGE_SIZE = 24


def decode_flat(data: list) -> Any:
    """Decode Rightmove's index-referenced PAGE_MODEL array.

    Every int inside a dict/list is an index into ``data``; bools/strings/floats are literal.
    Negative ints are sentinels (undefined/null/NaN) -> None.
    """
    memo: dict[int, Any] = {}

    def ref(j: Any) -> Any:
        if type(j) is int:
            return dec(j) if j >= 0 else None
        return j

    def dec(i: int, depth: int = 0) -> Any:
        if i in memo:
            return memo[i]
        if i >= len(data):
            return None
        v = data[i]
        if isinstance(v, dict):
            out: dict = {}
            memo[i] = out
            for k, j in v.items():
                out[k] = ref(j)
            return out
        if isinstance(v, list):
            out_l: list = []
            memo[i] = out_l
            out_l.extend(ref(j) for j in v)
            return out_l
        memo[i] = v
        return v

    return dec(0)


def page_model(html: str) -> dict | None:
    model = js_assignment(html, "window.__PAGE_MODEL") or js_assignment(html, "window.PAGE_MODEL")
    if isinstance(model, dict) and isinstance(model.get("data"), str):
        try:
            flat = json.loads(model["data"])
        except json.JSONDecodeError:
            return None
        return decode_flat(flat) if isinstance(flat, list) else flat
    return model


class RightmoveScraper(Scraper):
    name = "rightmove"
    label = "Rightmove"
    homepage = BASE

    def search_url(self, p: Profile, loc: SourceLocation, page: int) -> str | None:
        beds_min, beds_max = p.group_size, p.group_size + p.extra_bedrooms_allowed
        return (f"{BASE}/property-to-rent/find.html?locationIdentifier={quote(loc.rightmove or '')}"
                f"&minBedrooms={beds_min}&maxBedrooms={beds_max}&maxPrice={self.max_rent_pcm(p)}"
                f"&index={page * PAGE_SIZE}&sortType=6&includeLetAgreed=false")

    # ---------------------------------------------------------------- search
    def parse_search(self, html: str, url: str) -> list[RawListing]:
        soup = soupify(html)
        data = next_data(soup)
        props = dget(data, "props.pageProps.searchResults.properties") if data else None
        if props is None and data:
            props = find_dicts(data, "id", "bedrooms", any_of=("displayAddress", "propertyUrl"))
        if props is None:
            props = (js_assignment(html, "window.jsonModel") or {}).get("properties")
        out = [r for d in (props or []) if (r := self._from_search_json(d))]
        if out:
            return out
        # Fallback: cards located by /properties/{id} links
        for lid, href, card in cards_by_link(soup, r"/properties/(\d+)", BASE):
            text = text_of(card)
            r = RawListing(source=self.name, source_id=lid, url=f"{BASE}/properties/{lid}", card_text=text)
            r.bedrooms = parse_bedrooms(text)
            r.bathrooms = parse_bathrooms(text)
            m = re.search(r"£([\d,]+)\s*pcm", text)
            if m:
                r.price_amount, r.price_period = parse_money(m.group(1)), "pcm"
            addr = card.find("address")
            r.address = text_of(addr) if addr else None
            r.photos = card_images(card, BASE)
            r.let_agreed = "let agreed" in text.lower()
            out.append(r)
        return out

    def _from_search_json(self, d: dict) -> RawListing | None:
        lid = str(d.get("id") or "")
        if not lid.isdigit():
            return None
        r = RawListing(source=self.name, source_id=lid, url=f"{BASE}/properties/{lid}")
        r.title = first(d, "propertyTypeFullDescription", "heading", "propertySubType")
        r.address = first(d, "displayAddress")
        r.property_type = d.get("propertySubType")
        r.bedrooms = to_int(d.get("bedrooms"))
        baths = to_float(d.get("bathrooms"))
        r.bathrooms = baths if baths else None  # 0 means "not stated"
        r.lat = to_float(first(d, "location.latitude"))
        r.lng = to_float(first(d, "location.longitude"))
        amount = to_float(first(d, "price.amount"))
        freq = (first(d, "price.frequency") or "monthly").lower()
        if amount:
            r.price_amount = amount
            r.price_period = {"weekly": "pw", "monthly": "pcm", "yearly": "pa"}.get(freq, "pcm")
            if freq == "quarterly":
                r.price_amount, r.price_period = amount * 4, "pa"
            # Some student agents enter the per-person weekly price as the property price.
            if r.price_period == "pw" and amount < 300 and (r.bedrooms or 0) >= 3:
                r.price_per_person = True
        r.photos = [_photo(i) for i in (d.get("images") or first(d, "propertyImages.images") or []) if isinstance(i, dict)]
        r.photos = [p for p in r.photos if p]
        r.agent_name = first(d, "customer.branchDisplayName", "formattedBranchName", "customer.brandTradingName")
        r.agent_phone = first(d, "customer.contactTelephone")
        status = " ".join(str(x) for x in [d.get("displayStatus"), first(d, "listingUpdate.listingUpdateReason")] if x)
        r.let_agreed = "let agreed" in status.lower()
        r.student_let = True if d.get("students") else None
        kf = [k.get("description") if isinstance(k, dict) else k for k in (d.get("keyFeatures") or [])]
        r.key_features = [clean(k) for k in kf if k]
        r.card_text = clean(" ".join(str(x) for x in [d.get("summary"), first(d, "price.displayPrices.0.displayPrice"),
                                                       first(d, "price.displayPrices.1.displayPrice"), status] if x))
        avail = d.get("letAvailableDate")
        if avail:
            r.available_from = parse_date_fragment(str(avail)[:10], self.today)
        r.raw = {"search": {k: d.get(k) for k in ("firstVisibleDate", "addedOrReduced", "students", "letAvailableDate",
                                                  "displayStatus") if d.get(k) is not None}}
        return r

    # ---------------------------------------------------------------- detail
    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        model = page_model(html)
        pd = None
        if isinstance(model, dict):
            pd = model.get("propertyData") or next(iter(find_dicts(model, "lettings", "prices")), None)
        if not pd:
            nd = next_data(soupify(html))
            pd = next(iter(find_dicts(nd, "lettings", "prices")), None) if nd else None
        r = RawListing(source=self.name, source_id=base.source_id, url=base.url)
        if pd:
            return base.merge(self._from_property_data(pd, r))
        return base.merge(self._text_fallback(html, r))

    def _from_property_data(self, pd: dict, r: RawListing) -> RawListing:
        r.description = clean(re.sub(r"<[^>]+>", " ", first(pd, "text.description") or ""))
        r.key_features = [clean(k if isinstance(k, str) else (k or {}).get("description", "")) for k in (pd.get("keyFeatures") or []) if k]
        r.bedrooms = to_int(pd.get("bedrooms"))
        baths = to_float(pd.get("bathrooms"))
        r.bathrooms = baths if baths else None
        r.address = first(pd, "address.displayAddress")
        outcode, incode = first(pd, "address.outcode"), first(pd, "address.incode")
        if outcode and incode:
            r.postcode = f"{outcode} {incode}"
        r.lat = to_float(first(pd, "location.latitude"))
        r.lng = to_float(first(pd, "location.longitude"))
        r.photos = [p for p in (_photo(i) for i in (pd.get("images") or []) if isinstance(i, dict)) if p]
        r.floorplans = [p for p in (_photo(i) for i in (pd.get("floorplans") or []) if isinstance(i, dict)) if p]
        let = pd.get("lettings") or {}
        avail = let.get("letAvailableDate")
        if avail:
            if str(avail).lower().startswith("now"):
                r.available_from, r.available_now = self.today, True
            else:
                r.available_from = parse_date_fragment(str(avail), self.today)
        r.deposit = to_float(let.get("deposit"))
        r.min_term_months = to_int(let.get("minimumTermInMonths"))
        r.furnished = parse_furnished(let.get("furnishType") or "")
        if str(let.get("letType") or "").lower().startswith("student"):
            r.student_let = True
        primary = first(pd, "prices.primaryPrice")
        if primary:
            r.price_amount = parse_money(primary)
            r.price_period = "pw" if re.search(r"\bpw\b", primary.lower()) else "pcm"
            if r.price_period == "pw" and (r.price_amount or 0) < 300 and (r.bedrooms or 0) >= 3:
                r.price_per_person = True
        r.agent_name = first(pd, "customer.branchDisplayName", "customer.companyName")
        r.agent_phone = first(pd, "contactInfo.telephoneNumbers.localNumber")
        r.property_type = first(pd, "propertySubType")
        status = " ".join(str(x) for x in [first(pd, "status.displayStatus"), first(pd, "listingHistory.listingUpdateReason")] if x)
        r.let_agreed = "let agreed" in status.lower() or bool(first(pd, "status.archived"))
        if first(pd, "livingCosts.councilTaxBand"):
            r.raw = {"council_tax_band": first(pd, "livingCosts.councilTaxBand")}
        stations = pd.get("nearestStations") or []
        if stations:
            r.raw = {**r.raw, "stations": [{"name": s.get("name"), "miles": s.get("distance")} for s in stations[:3] if isinstance(s, dict)]}
        r.card_text = clean(" ".join(x for x in [primary or "", first(pd, "prices.secondaryPrice") or "",
                                                 first(pd, "text.propertyPhrase") or ""] if isinstance(x, str)))
        return r

    def _text_fallback(self, html: str, r: RawListing) -> RawListing:
        soup = soupify(html)
        pairs = label_values(soup)
        page_text = text_of(soup.find("main") or soup)
        r.card_text = page_text[:6000]
        v = pairs.get("let available date")
        if v:
            r.available_now = v.lower().startswith("now")
            r.available_from = self.today if r.available_now else parse_date_fragment(v, self.today)
        else:
            r.available_from, r.available_now = parse_available(page_text, self.today)
        r.deposit = parse_money(pairs.get("deposit"))
        r.furnished = parse_furnished(pairs.get("furnish type", ""))
        m = re.search(r"BATHROOMS\s*(\d+)", page_text)
        r.bathrooms = float(m.group(1)) if m else parse_bathrooms(page_text)
        m = re.search(r"BEDROOMS\s*(\d+)", page_text)
        r.bedrooms = int(m.group(1)) if m else parse_bedrooms(page_text)
        r.photos = [u for u in image_urls(html, "media.rightmove.co.uk") if "/property-photo/" in u and "_max_" not in u]
        r.postcode, _ = parse_postcode(page_text[:3000])
        r.let_agreed = bool(re.search(r"\blet agreed\b", page_text[:3000], re.I))
        return r


def _photo(i: dict) -> str | None:
    """Full-size photo URL. Search results give a relative 'url' (full size) plus a cropped 'srcUrl'."""
    u = i.get("url") or i.get("srcUrl")
    if not u:
        return None
    if not u.startswith("http"):
        u = MEDIA + u.lstrip("/")
    u = re.sub(r"/dir/crop/[^/]+/", "/", u)
    u = re.sub(r"_max_\d+x\d+(\.\w+)$", r"\1", u)
    return u.replace(":443", "")
