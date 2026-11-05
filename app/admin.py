"""The admin site (house-admin.tamairnv.co.uk): every finder site, their setup, logs, traffic and health.

Only reachable on HF_ADMIN_HOST, and only with a valid Cloudflare Access login for the admin app whose email is
in HF_ADMIN_EMAILS (checked in main.HostRouter before a request gets here).
"""
from __future__ import annotations

import os
import platform
import resource
import shutil
import time
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from sqlalchemy import desc, select

from . import access, ai, applog, cloudflare, netstats, scheduler, sites, tenancy
from .admin_db import Site, admin_session
from .config import get_settings
from .db import session_scope
from .models import AIUsage, EmailLog, Listing, ScrapeRun, utcnow

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
STARTED = time.time()

admin_app = FastAPI(title="House finder admin", docs_url=None, redoc_url=None, openapi_url=None)
admin_app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def _me(request: Request) -> str | None:
    return request.scope.get("state", {}).get("viewer")


@admin_app.get("/healthz")
def healthz():
    return {"ok": True}


@admin_app.get("/", response_class=HTMLResponse)
def home(request: Request):
    from .main import asset_version
    return templates.TemplateResponse(request, "admin.html", {"v": asset_version(), "me": _me(request),
                                                              "domain": get_settings().site_domain})


# ---------------------------------------------------------------- overview / health

def _rows() -> list[Site]:
    with admin_session() as s:
        return list(s.scalars(select(Site).order_by(Site.is_default.desc(), Site.slug)))


def health() -> dict:
    st = get_settings()
    disk = shutil.disk_usage(st.data_dir)
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        rss_kb = int(next(line.split()[1] for line in open("/proc/self/status") if line.startswith("VmRSS")))
    except (OSError, StopIteration):
        pass
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    sched = scheduler._scheduler
    jobs = {j.id: j.next_run_time.isoformat() if j.next_run_time else None for j in sched.get_jobs()} if sched else {}
    return {
        "uptime_s": int(time.time() - STARTED), "version": os.environ.get("HF_VERSION") or _git_version(),
        "python": platform.python_version(), "host": platform.node(),
        "scheduler": {"running": sched is not None, "jobs": jobs, "scraping": sorted(scheduler._running)},
        "smtp": st.smtp_configured, "ai": ai.provider() if ai.enabled() else None, "cloudflare_token": access.token_set(),
        "routing_api": bool(st.ors_api_key), "playwright": st.playwright_enabled,
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        "memory_rss": rss_kb * 1024, "load": [round(x, 2) for x in load], "cpus": os.cpu_count(),
        "data_dir_bytes": _dir_size(st.data_dir), "photos_bytes": _dir_size(st.data_dir / "photos"),
    }


def _git_version() -> str | None:
    for p in (HERE.parent / ".version", HERE.parent / ".git" / "HEAD"):
        try:
            v = p.read_text().strip()
        except OSError:
            continue
        if v.startswith("ref: "):
            try:
                return (HERE.parent / ".git" / v[5:]).read_text().strip()[:10]
            except OSError:
                return None
        return v[:40]
    return None


_size_cache: dict[str, tuple[float, int]] = {}


def _dir_size(path: Path) -> int:
    key = str(path)
    hit = _size_cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    total = 0
    if path.exists():
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    _size_cache[key] = (time.time(), total)
    return total


@admin_app.get("/api/overview")
def overview(request: Request):
    return {"me": _me(request), "health": health(), "sites": [sites.site_dict(r, with_usage=True) for r in _rows()],
            "domain": get_settings().site_domain}


# ---------------------------------------------------------------- sites

@admin_app.get("/api/sites")
def list_sites():
    return [sites.site_dict(r, with_usage=True) for r in _rows()]


def _row(slug: str) -> Site:
    with admin_session() as s:
        row = s.scalar(select(Site).where(Site.slug == slug))
    if row is None:
        raise HTTPException(404, "No such site")
    return row


@admin_app.get("/api/sites/{slug}")
def get_site(slug: str):
    return sites.site_dict(_row(slug), with_usage=True)


@admin_app.get("/api/check-slug")
def check_slug(slug: str):
    problem = sites.slug_problem(slug)
    return {"ok": problem is None, "problem": problem, "hostname": sites.hostname_for(slug.strip().lower())}


@admin_app.post("/api/sites")
def create_site(body: dict):
    try:
        new = sites.NewSite.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors()))
    try:
        sites.create(new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if access.token_set():
        sites.run_setup_async(new.slug, new.include_admins)
    else:
        sites._step(new.slug, "dns", False, "No Cloudflare token (HF_CF_API_TOKEN), so DNS/tunnel/login need doing by hand.")
    return sites.site_dict(_row(new.slug))


class SetupIn(BaseModel):
    include_admins: bool = True


@admin_app.post("/api/sites/{slug}/setup")
def retry_setup(slug: str, body: SetupIn | None = None):
    row = _row(slug)
    if row.is_default:
        raise HTTPException(400, "The original site is already set up.")
    if not access.token_set():
        raise HTTPException(400, "Add HF_CF_API_TOKEN to .env first.")
    sites.run_setup_async(slug, (body or SetupIn()).include_admins)
    return {"started": True}


class SitePatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    strict_auth: bool | None = None
    admin_emails: str | None = None
    notes: str | None = None


@admin_app.patch("/api/sites/{slug}")
def patch_site(slug: str, body: SitePatch):
    _row(slug)
    try:
        sites.update(slug, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return sites.site_dict(_row(slug))


@admin_app.delete("/api/sites/{slug}")
def delete_site(slug: str, confirm: str = ""):
    _row(slug)
    if confirm != slug:
        raise HTTPException(400, "Type the site's name to confirm.")
    try:
        return sites.delete(slug)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _info(slug: str) -> tenancy.SiteInfo:
    info = tenancy.by_slug(slug)
    if info is None:
        raise HTTPException(404, "No such site")
    return info


@admin_app.post("/api/sites/{slug}/scrape")
def scrape_site(slug: str):
    started = scheduler.trigger_scrape_now(_info(slug))
    return {"started": started, "message": "Scrape started" if started else "A scrape is already running"}


@admin_app.post("/api/sites/{slug}/check")
def check_site(slug: str):
    return cloudflare.check_live(_info(slug).hostname)


@admin_app.get("/api/sites/{slug}/access")
def site_access(slug: str):
    info = _info(slug)
    out = {"configured": access.token_set(), "emails": [], "error": None}
    if out["configured"]:
        try:
            out.update(access.list_emails(info))
        except access.AccessError as e:
            out["error"] = str(e)
    return out


class EmailIn(BaseModel):
    email: str


@admin_app.post("/api/sites/{slug}/access")
def site_access_add(slug: str, body: EmailIn):
    try:
        return {"emails": access.add_email(body.email, _info(slug))}
    except access.AccessError as e:
        raise HTTPException(400, str(e))


@admin_app.delete("/api/sites/{slug}/access/{email}")
def site_access_remove(slug: str, email: str, request: Request):
    protect = {_me(request)} if _me(request) else set()
    try:
        return {"emails": access.remove_email(email, protect, _info(slug))}
    except access.AccessError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- logs

def _each_site(slug: str | None):
    for info in tenancy.all_sites():
        if not slug or info.slug == slug:
            yield info


@admin_app.get("/api/logs/ai")
def ai_log(days: int = 30, site: str | None = None):
    """AI requests across sites: who asked, about what, and the API-equivalent cost."""
    since = utcnow() - timedelta(days=days)
    rows, people = [], {}
    for info in _each_site(site):
        try:
            with tenancy.use(info), session_scope() as s:
                got = s.execute(select(AIUsage, Listing.title).outerjoin(Listing, Listing.id == AIUsage.listing_id)
                                .where(AIUsage.at >= since).order_by(desc(AIUsage.id)).limit(500)).all()
        except Exception:  # noqa: BLE001
            continue
        for u, title in got:
            key = (u.user_email or "", info.slug)
            p = people.setdefault(key, {"user": u.user_email, "site": info.slug, "requests": 0, "failed": 0,
                                        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "last": None})
            p["requests"] += 1
            p["failed"] += 0 if u.ok else 1
            p["input_tokens"] += u.input_tokens or 0
            p["output_tokens"] += u.output_tokens or 0
            p["cost_usd"] += u.cost_usd or 0
            p["last"] = max(p["last"] or "", u.at.isoformat())
            rows.append({"at": u.at.isoformat(), "site": info.slug, "site_url": info.base_url, "user": u.user_email,
                         "listing_id": u.listing_id, "title": title, "kind": u.kind, "question": u.question, "ok": u.ok,
                         "error": u.error, "provider": u.provider, "model": u.model, "input_tokens": u.input_tokens,
                         "output_tokens": u.output_tokens, "cost_usd": u.cost_usd, "duration_ms": u.duration_ms})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return {"days": days, "people": sorted(people.values(), key=lambda p: -p["requests"]), "rows": rows[:500]}


@admin_app.get("/api/logs/scrapes")
def scrape_log(site: str | None = None, limit: int = 100):
    rows = []
    for info in _each_site(site):
        try:
            with tenancy.use(info), session_scope() as s:
                got = s.scalars(select(ScrapeRun).order_by(desc(ScrapeRun.id)).limit(limit)).all()
        except Exception:  # noqa: BLE001
            continue
        rows += [{"site": info.slug, "id": r.id, "source": r.source, "started_at": r.started_at.isoformat(),
                  "finished_at": r.finished_at.isoformat() if r.finished_at else None, "ok": r.ok, "requests": r.pages,
                  "found": r.found, "new": r.new, "updated": r.updated, "error": (r.error or "")[:1500]} for r in got]
    rows.sort(key=lambda r: r["started_at"], reverse=True)
    return rows[:limit]


@admin_app.get("/api/logs/emails")
def email_log(site: str | None = None, limit: int = 100):
    rows = []
    for info in _each_site(site):
        try:
            with tenancy.use(info), session_scope() as s:
                got = s.scalars(select(EmailLog).order_by(desc(EmailLog.id)).limit(limit)).all()
        except Exception:  # noqa: BLE001
            continue
        rows += [{"site": info.slug, "sent_at": e.sent_at.isoformat(), "kind": e.kind, "subject": e.subject, "ok": e.ok,
                  "error": e.error, "count": len(e.listing_ids or [])} for e in got]
    rows.sort(key=lambda r: r["sent_at"], reverse=True)
    return rows[:limit]


@admin_app.get("/api/logs/app")
def app_log(level: str | None = None, site: str | None = None, q: str | None = None, limit: int = 400):
    return applog.records(level=level, site=site, q=q, limit=min(limit, 2000))


@admin_app.get("/api/network")
def network(hours: int = 24):
    return netstats.summary(max(1, min(hours, 24 * 30)))
