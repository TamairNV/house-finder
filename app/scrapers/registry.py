from __future__ import annotations

from .afs import AFSScraper
from .base import Fetcher, Scraper
from .openrent import OpenRentScraper
from .rightmove import RightmoveScraper
from .spareroom import SpareRoomScraper
from .sturents import StuRentsScraper
from .unihomes import UniHomesScraper
from .zoopla import ZooplaScraper

SCRAPERS: dict[str, type[Scraper]] = {
    s.name: s for s in [RightmoveScraper, ZooplaScraper, OpenRentScraper, SpareRoomScraper,
                        StuRentsScraper, AFSScraper, UniHomesScraper]
}


def get_scraper(name: str, fetcher: Fetcher | None = None) -> Scraper:
    return SCRAPERS[name](fetcher)
