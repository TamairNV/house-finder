"""Several finder sites in one app, picked by hostname.

Each site (a group of friends hunting together) has its own SQLite database, so listings, profile,
recipients, votes and AI history never mix. The first site ("house") is the original database and
its hostname comes from HF_BASE_URL. Other sites live in ``data/sites/<slug>/house_finder.db``.

The request middleware (and the scheduler, for background jobs) sets ``current`` to the active site;
``db.session_scope()`` and everything else follows it.
"""
from __future__ import annotations

import contextvars
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

from .config import get_settings

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
RESERVED = {"admin", "house-admin", "www", "api", "mail", "static", "sites", "default"}


@dataclass(frozen=True)
class SiteInfo:
    """Snapshot of one site, safe to pass between threads."""
    slug: str
    name: str
    hostname: str
    db_url: str
    is_default: bool = False
    enabled: bool = True
    strict_auth: bool = True  # every request must carry a valid Access login for this site
    access_aud: str | None = None
    access_app_id: str | None = None
    access_policy_id: str | None = None
    admin_emails: frozenset[str] = field(default_factory=frozenset)  # can edit this site's login list

    @property
    def base_url(self) -> str:
        if self.is_default:
            return get_settings().base_url
        return f"https://{self.hostname}"


def default_slug() -> str:
    return get_settings().default_site_slug


def default_site() -> SiteInfo:
    st = get_settings()
    return SiteInfo(slug=st.default_site_slug, name="House finder", hostname=_host(st.base_url) or "localhost",
                    db_url=st.db_url, is_default=True, strict_auth=False, access_aud=st.cf_access_aud,
                    access_app_id=st.cf_access_app_id, access_policy_id=st.cf_access_policy_id)


def site_db_url(slug: str) -> str:
    path = get_settings().data_dir / "sites" / slug
    path.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(path / 'house_finder.db').as_posix()}"


def _host(url_or_host: str | None) -> str:
    if not url_or_host:
        return ""
    h = url_or_host if "//" not in url_or_host else (urlparse(url_or_host).hostname or "")
    return h.split(":")[0].strip().lower().rstrip(".")


_current: contextvars.ContextVar[SiteInfo | None] = contextvars.ContextVar("site", default=None)


def current() -> SiteInfo:
    return _current.get() or _overlay_default()


def set_current(site: SiteInfo | None) -> contextvars.Token:
    return _current.set(site)


@contextmanager
def use(site: SiteInfo) -> Iterator[SiteInfo]:
    token = _current.set(site)
    try:
        yield site
    finally:
        _current.reset(token)


# ---------------------------------------------------------------- registry (admin DB)

_cache: tuple[float, list[SiteInfo]] | None = None
CACHE_SECONDS = 20


def invalidate() -> None:
    global _cache
    _cache = None


def _from_row(row) -> SiteInfo:
    emails = frozenset(e.strip().lower() for e in (row.admin_emails or "").split(",") if e.strip())
    if row.is_default:  # only the row's own values here; _refresh_default() fills the rest from settings
        base = default_site()
        return replace(base, slug=row.slug, name=row.name or base.name, enabled=True, access_aud=row.access_aud,
                       access_app_id=row.access_app_id, access_policy_id=row.access_policy_id, admin_emails=emails)
    return SiteInfo(slug=row.slug, name=row.name, hostname=row.hostname.lower(), db_url=site_db_url(row.slug),
                    is_default=False, enabled=row.enabled, strict_auth=row.strict_auth, access_aud=row.access_aud,
                    access_app_id=row.access_app_id, access_policy_id=row.access_policy_id, admin_emails=emails)


def all_sites(include_disabled: bool = True) -> list[SiteInfo]:
    """Every site, default first. Cached briefly; call invalidate() after changing one."""
    global _cache
    if _cache is None or time.monotonic() - _cache[0] > CACHE_SECONDS:
        from . import admin_db
        try:
            rows = admin_db.list_site_rows()
        except Exception:  # noqa: BLE001  (admin DB missing in odd setups: fall back to the original site)
            rows = []
        sites = [_from_row(r) for r in rows]
        if not any(s.is_default for s in sites):
            sites.insert(0, replace(default_site(), access_aud=None, access_app_id=None, access_policy_id=None))
        sites.sort(key=lambda s: (not s.is_default, s.slug))
        _cache = (time.monotonic(), sites)
    sites = _cache[1]
    # The default's hostname/database follow the live settings (tests and .env changes).
    sites = [_refresh_default(s) if s.is_default else s for s in sites]
    return sites if include_disabled else [s for s in sites if s.enabled]


def _refresh_default(s: SiteInfo) -> SiteInfo:
    d = default_site()
    return replace(s, hostname=d.hostname, db_url=d.db_url, access_aud=s.access_aud or d.access_aud,
                   access_app_id=s.access_app_id or d.access_app_id,
                   access_policy_id=s.access_policy_id or d.access_policy_id)


def _overlay_default() -> SiteInfo:
    try:
        return next(s for s in all_sites() if s.is_default)
    except StopIteration:
        return default_site()


def by_slug(slug: str) -> SiteInfo | None:
    return next((s for s in all_sites() if s.slug == slug), None)


def admin_host() -> str:
    return _host(get_settings().admin_host)


LOCAL_HOSTS = {"localhost", "127.0.0.1", "testserver", "0.0.0.0", "::1", "house-finder"}


def resolve(host_header: str | None) -> SiteInfo | str | None:
    """Map a Host header to a site, the string "admin", or None (no site here)."""
    host = _host(host_header)
    if host and host == admin_host():
        return "admin"
    for s in all_sites():
        if s.hostname == host:
            return s if s.enabled else None
    if not host or host in LOCAL_HOSTS or re.fullmatch(r"[\d.]+", host) or host.endswith(".local") \
            or host.startswith("100.") or "." not in host:
        return _overlay_default()  # LAN / Tailscale / localhost reach the original site
    return None
