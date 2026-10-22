from __future__ import annotations

import logging
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..profile import Profile, SourceLocation

log = logging.getLogger(__name__)


class BlockedError(RuntimeError):
    """The site served a bot challenge / captcha / 403."""


@dataclass
class RawListing:
    source: str
    source_id: str
    url: str
    title: str | None = None
    address: str | None = None
    area: str | None = None
    postcode: str | None = None
    lat: float | None = None
    lng: float | None = None
    property_type: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    furnished: str | None = None
    epc: str | None = None
    # Price as the site states it; textparse.guess_rent reconciles with free text.
    price_amount: float | None = None
    price_period: str | None = None  # pw|pcm|pa
    price_per_person: bool = False
    bills_included: bool | None = None
    deposit: float | None = None
    min_term_months: int | None = None
    available_from: date | None = None
    available_now: bool = False
    description: str | None = None
    key_features: list[str] = field(default_factory=list)
    agent_name: str | None = None
    agent_phone: str | None = None
    photos: list[str] = field(default_factory=list)
    floorplans: list[str] = field(default_factory=list)
    listed_at: datetime | None = None
    let_agreed: bool = False
    student_let: bool | None = None
    card_text: str = ""  # raw text of the search card / page, mined for prices etc.
    detail_fetched: bool = False
    raw: dict = field(default_factory=dict)

    def merge(self, other: "RawListing") -> "RawListing":
        """Fill gaps in self with values from other (detail page beats search card)."""
        for k, v in other.__dict__.items():
            if k in ("source", "source_id"):
                continue
            cur = getattr(self, k)
            if k in ("photos", "floorplans", "key_features"):
                if v and len(v) >= len(cur or []):
                    setattr(self, k, v)
            elif k == "card_text":
                if v and v not in cur:
                    setattr(self, k, (cur + " \n " + v).strip())
            elif k == "raw":
                self.raw = {**cur, **v}
            elif v not in (None, "", False) or cur is None:
                setattr(self, k, v)
        return self

    @property
    def text_blob(self) -> str:
        return " \n ".join(filter(None, [self.title, self.address, self.card_text, " . ".join(self.key_features),
                                         self.description]))


CHALLENGE_MARKERS = [
    "cf-chl", "challenge-platform", "Just a moment...", "Attention Required! | Cloudflare",
    "px-captcha", "_Incapsula_Resource", "captcha-delivery.com", "Access Denied", "Pardon Our Interruption",
]


class Fetcher:
    """Polite HTTP client: per-host delay with jitter, retries, challenge detection, optional dumps."""

    def __init__(self, source: str):
        s = get_settings()
        self.source = source
        self.delay = s.request_delay_seconds
        self.dump = s.dump_html
        self.dump_dir = s.data_dir / "dumps" / source
        self.client = httpx.Client(
            headers={
                "User-Agent": s.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            },
            follow_redirects=True,
            timeout=30,
            http2=False,
        )
        self._last: dict[str, float] = {}
        self.requests = 0
        self._browser = None

    def close(self) -> None:
        self.client.close()
        if self._browser is not None:
            self._browser.close()

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.delay * random.uniform(0.8, 1.6)
            remaining = gap - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last[host] = time.monotonic()

    def get(self, url: str, *, params: dict | None = None, json_api: bool = False, browser: bool = False) -> str:
        host = urlparse(url).netloc
        if browser and get_settings().playwright_enabled:
            return self._browser_get(url)
        for attempt in range(3):
            self._wait(host)
            self.requests += 1
            try:
                headers = {"Accept": "application/json"} if json_api else None
                r = self.client.get(url, params=params, headers=headers)
            except httpx.HTTPError as e:
                log.warning("[%s] %s failed (%s), attempt %d", self.source, url, e, attempt + 1)
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (429, 503):
                wait = int(r.headers.get("Retry-After", "0") or 0) or 30 * (attempt + 1)
                log.warning("[%s] %s -> %s, backing off %ss", self.source, url, r.status_code, wait)
                time.sleep(min(wait, 120))
                continue
            body = r.text
            self._maybe_dump(str(r.url), body)
            if r.status_code == 403 or (not json_api and any(m in body[:20000] for m in CHALLENGE_MARKERS)
                                         and len(body) < 60000):
                raise BlockedError(f"{self.source}: bot challenge / {r.status_code} at {url}")
            if r.status_code == 404:
                raise FileNotFoundError(url)
            r.raise_for_status()
            return body
        raise RuntimeError(f"{self.source}: giving up on {url}")

    def _browser_get(self, url: str) -> str:
        """Headless Chromium for sites behind JS challenges. Needs `pip install playwright`."""
        from playwright.sync_api import sync_playwright  # optional dependency

        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._ctx = self._browser.new_context(user_agent=get_settings().user_agent, locale="en-GB")
        self._wait(urlparse(url).netloc)
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            html = page.content()
        finally:
            page.close()
        self._maybe_dump(url, html)
        if any(m in html[:20000] for m in CHALLENGE_MARKERS) and len(html) < 60000:
            raise BlockedError(f"{self.source}: challenge even in browser at {url}")
        return html

    def _maybe_dump(self, url: str, body: str) -> None:
        if not self.dump:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", urlparse(url).path + "_" + urlparse(url).query)[:150]
        (self.dump_dir / f"{int(time.time())}_{name}.html").write_text(body, encoding="utf-8")


class Scraper(ABC):
    name: str = ""
    label: str = ""
    homepage: str = ""
    weekly_is_per_person: bool = False  # student sites quote £X/week per person
    uses_browser: bool = False

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher(self.name)
        self.today = date.today()

    @abstractmethod
    def search_url(self, profile: Profile, loc: SourceLocation, page: int) -> str | None:
        """URL of search results page ``page`` (0-based), or None if no more pages."""

    @abstractmethod
    def parse_search(self, html: str, url: str) -> list[RawListing]:
        ...

    def parse_detail(self, html: str, base: RawListing) -> RawListing:
        return base

    def location_key(self, loc: SourceLocation) -> str | None:
        return getattr(loc, self.name, None)

    def search(self, profile: Profile, loc: SourceLocation, max_pages: int) -> Iterator[RawListing]:
        if not self.location_key(loc):
            return
        seen: set[str] = set()
        for page in range(max_pages):
            url = self.search_url(profile, loc, page)
            if not url:
                break
            html = self.fetcher.get(url, browser=self.uses_browser)
            items = self.parse_search(html, url)
            fresh = [i for i in items if i.source_id not in seen]
            log.info("[%s] page %d: %d listings (%d new on this run)", self.name, page, len(items), len(fresh))
            if not fresh:
                break
            for it in fresh:
                seen.add(it.source_id)
                yield it

    def fetch_detail(self, raw: RawListing) -> RawListing:
        html = self.fetcher.get(raw.url, browser=self.uses_browser)
        detailed = self.parse_detail(html, raw)
        detailed.detail_fetched = True
        return detailed

    # helpers ---------------------------------------------------------------
    def max_rent_pcm(self, p: Profile) -> int:
        """Generous whole-property ceiling for portal search filters (rent only, no bills)."""
        beds = p.group_size + p.extra_bedrooms_allowed
        return int(round(p.budget_max_pppw * beds * 52 / 12 * 1.05, -1))

    def max_rent_pppw(self, p: Profile) -> int:
        return int(p.budget_max_pppw + 5)
