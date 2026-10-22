"""Fetch listing photos with an on-disk cache (used by the /img proxy and the AI overview)."""
from __future__ import annotations

import hashlib

import httpx

from .config import get_settings

ALLOWED_IMG_HOSTS = ("rightmove.co.uk", "zoocdn.com", "zoopla.co.uk", "openrent.co.uk", "spareroom.co.uk",
                     "sturents.com", "accommodationforstudents.com", "unihomes.co.uk", "cloudfront.net",
                     "amazonaws.com", "imgix.net", "picsum.photos")


class PhotoError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def allowed(u: str) -> bool:
    host = httpx.URL(u).host if u.startswith("http") else ""
    return any(host.endswith(h) for h in ALLOWED_IMG_HOSTS)


def fetch(u: str) -> tuple[bytes, str]:
    """Return (bytes, content type), from the cache when we have it."""
    if not allowed(u):
        raise PhotoError(400, "host not allowed")
    s = get_settings()
    key = hashlib.sha1(u.encode()).hexdigest()
    path = s.data_dir / "photos" / key[:2] / key
    if path.exists():
        tpath = path.with_suffix(".type")
        return path.read_bytes(), (tpath.read_text() if tpath.exists() else "image/jpeg")
    host = httpx.URL(u).host
    try:
        r = httpx.get(u, headers={"User-Agent": s.user_agent, "Referer": f"https://{host}/"}, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        raise PhotoError(502, "upstream image fetch failed")
    ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
    if not ctype.startswith("image/"):
        raise PhotoError(502, "not an image")
    if s.cache_photos:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        path.with_suffix(".type").write_text(ctype)
    return r.content, ctype
