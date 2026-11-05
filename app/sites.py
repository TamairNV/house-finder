"""Create, set up, change and remove finder sites (used by the admin site and the CLI)."""
from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from . import access, admin_db, cloudflare, tenancy
from .admin_db import Site, admin_session
from .config import get_settings
from .db import init_db, session_scope
from .models import AIUsage, EmailLog, Listing, Recipient, ScrapeRun, utcnow
from .profile import Person, Profile
from .store import load_profile, save_profile

log = logging.getLogger(__name__)

STEPS = ["database", "dns", "tunnel", "access", "check"]
STEP_LABELS = {"database": "Create the site's database", "dns": "Add the DNS record",
               "tunnel": "Route the tunnel to the app", "access": "Set up the email login",
               "check": "Check it's live and locked"}
_setup_lock = threading.Lock()


class Invitee(BaseModel):
    email: str
    name: str | None = None
    digest: bool = True  # gets the daily email
    owner: bool = False  # can change the site's login list from its own Settings

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not access.EMAIL_RE.match(v):
            raise ValueError(f"{v!r} isn't an email address")
        return v


class NewSite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str
    people: list[Invitee] = Field(default_factory=list)
    copy_from: str | None = None  # slug of a site whose search settings to start from
    group_size: int | None = None
    budget_pppw: float | None = None
    include_admins: bool = True  # Tamair can log in too
    digest_hour: int = 18
    notes: str | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not tenancy.SLUG_RE.match(v) or v in tenancy.RESERVED:
            raise ValueError("Use 2-32 lowercase letters, numbers or dashes (not a reserved name)")
        return v


def hostname_for(slug: str) -> str:
    return f"{slug}.{get_settings().site_domain}"


def slug_problem(slug: str) -> str | None:
    slug = (slug or "").strip().lower()
    if not tenancy.SLUG_RE.match(slug) or len(slug) < 2:
        return "Use 2-32 lowercase letters, numbers or dashes."
    if slug in tenancy.RESERVED or hostname_for(slug) == tenancy.admin_host():
        return "That name is reserved."
    with admin_session() as s:
        if s.scalar(select(Site).where((Site.slug == slug) | (Site.hostname == hostname_for(slug)))):
            return "A site with that name already exists."
    return None


def _step(slug: str, step: str, ok: bool | None, msg: str = "") -> None:
    with admin_session() as s:
        row = s.scalar(select(Site).where(Site.slug == slug))
        setup = dict(row.setup or {})
        setup[step] = {"ok": ok, "msg": msg, "at": utcnow().isoformat()}
        row.setup = setup
    tenancy.invalidate()


def create(body: NewSite) -> Site:
    problem = slug_problem(body.slug)
    if problem:
        raise ValueError(problem)
    owners = ",".join(p.email for p in body.people if p.owner)
    with admin_session() as s:
        row = Site(slug=body.slug, name=body.name.strip(), hostname=hostname_for(body.slug), enabled=True, strict_auth=True,
                   admin_emails=owners, emails=[p.model_dump() for p in body.people], notes=body.notes,
                   setup={k: {"ok": None, "msg": "waiting", "at": None} for k in STEPS})
        s.add(row)
    tenancy.invalidate()
    _seed(body)
    return row


def _seed(body: NewSite) -> None:
    site = tenancy.by_slug(body.slug)
    template = tenancy.by_slug(body.copy_from) if body.copy_from else None
    if template is not None:
        with tenancy.use(template), session_scope() as s:
            base = load_profile(s)
    else:
        base = Profile()
    first_place = next((pl for p in base.people for pl in p.places), None)
    people = [Person(name=p.name or p.email.split("@")[0].title(), email=p.email,
                     places=[first_place.model_copy()] if first_place else []) for p in body.people]
    profile = base.model_copy(update={"name": body.name, "people": people or base.people})
    if body.group_size:
        profile.group_size = body.group_size
    if body.budget_pppw:
        profile.budget_pppw = body.budget_pppw
    profile = Profile.model_validate(profile.model_dump())
    with tenancy.use(site):
        init_db()
        with session_scope() as s:
            save_profile(s, profile)
            for p in body.people:
                if p.digest:
                    s.add(Recipient(name=p.name or p.email.split("@")[0].title(), email=p.email, digest_frequency="daily",
                                    digest_hour=body.digest_hour, instant_great=False))
    _step(body.slug, "database", True, f"{len(people)} people, {sum(p.digest for p in body.people)} on the daily email")


def run_setup(slug: str, include_admins: bool = True) -> dict:
    """Run every unfinished step (each is safe to repeat). Returns the setup dict."""
    with _setup_lock:
        with admin_session() as s:
            row = s.scalar(select(Site).where(Site.slug == slug))
            if row is None:
                raise ValueError("No such site")
            if row.is_default:
                return row.setup
            hostname, name, invited = row.hostname, row.name, [p["email"] for p in row.emails or []]
        emails = invited + (sorted(get_settings().admins) if include_admins else [])
        setup = {}
        for step in STEPS[1:]:
            try:
                if step == "dns":
                    rid = cloudflare.ensure_dns(hostname)
                    _save(slug, dns_record_id=rid)
                    _step(slug, step, True, f"{hostname} → tunnel")
                elif step == "tunnel":
                    changed = cloudflare.ensure_ingress(hostname)
                    _step(slug, step, True, "Route added" if changed else "Route already there")
                elif step == "access":
                    res = cloudflare.ensure_access_app(name, hostname, emails)
                    _save(slug, access_app_id=res["app_id"], access_aud=res["aud"], access_policy_id=res["policy_id"])
                    access.forget(hostname)
                    _step(slug, step, True, f"{len(set(emails))} email(s) can log in")
                elif step == "check":
                    res = cloudflare.check_live(hostname)
                    _step(slug, step, res["ok"], res["msg"])
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] setup step %s failed: %s", slug, step, e)
                _step(slug, step, False, str(e)[:300])
                if step != "check":
                    break  # later steps depend on this one
        with admin_session() as s:
            setup = s.scalar(select(Site).where(Site.slug == slug)).setup
        return setup


def run_setup_async(slug: str, include_admins: bool = True) -> None:
    threading.Thread(target=run_setup, args=(slug, include_admins), daemon=True, name=f"setup-{slug}").start()


def _save(slug: str, **fields) -> None:
    with admin_session() as s:
        row = s.scalar(select(Site).where(Site.slug == slug))
        for k, v in fields.items():
            setattr(row, k, v)
    tenancy.invalidate()


def update(slug: str, **fields) -> Site:
    allowed = {"name", "enabled", "strict_auth", "admin_emails", "notes"}
    with admin_session() as s:
        row = s.scalar(select(Site).where(Site.slug == slug))
        if row is None:
            raise ValueError("No such site")
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if row.is_default and k in ("enabled", "strict_auth"):
                continue  # the original site stays on and keeps LAN access
            if k == "admin_emails":
                v = ",".join(sorted({e.strip().lower() for e in v.split(",") if e.strip()}))
            setattr(row, k, v)
    tenancy.invalidate()
    return row


def delete(slug: str, remove_cloudflare: bool = True) -> dict:
    """Take a site offline: Cloudflare bits removed, database moved to data/sites/_deleted/."""
    with admin_session() as s:
        row = s.scalar(select(Site).where(Site.slug == slug))
        if row is None:
            raise ValueError("No such site")
        if row.is_default:
            raise ValueError("The original site can't be deleted here.")
        info = {"hostname": row.hostname, "app": row.access_app_id, "policy": row.access_policy_id}
    done = []
    if remove_cloudflare and access.token_set():
        for label, fn in (("access", lambda: cloudflare.remove_access_app(info["app"], info["policy"])),
                          ("tunnel", lambda: cloudflare.remove_ingress(info["hostname"])),
                          ("dns", lambda: cloudflare.remove_dns(info["hostname"]))):
            try:
                fn()
                done.append(label)
            except Exception as e:  # noqa: BLE001
                done.append(f"{label} failed: {e}")
    from . import db
    src = get_settings().data_dir / "sites" / slug
    for url in [u for u in list(db._engines) if f"/sites/{slug}/" in u]:
        db._engines.pop(url)[0].dispose()
    if src.exists():
        dest = get_settings().data_dir / "sites" / "_deleted" / f"{slug}-{datetime.now():%Y%m%d-%H%M%S}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        done.append(f"database archived to {dest.relative_to(get_settings().data_dir)}")
    with admin_session() as s:
        s.delete(s.scalar(select(Site).where(Site.slug == slug)))
    tenancy.invalidate()
    access.forget(info["hostname"])
    return {"done": done}


# ---------------------------------------------------------------- reading

def _db_size(site: tenancy.SiteInfo) -> int:
    path = Path(site.db_url.removeprefix("sqlite:///"))
    return sum(p.stat().st_size for p in path.parent.glob(path.name + "*") if p.is_file()) if path.exists() else 0


def usage(site: tenancy.SiteInfo) -> dict:
    """Numbers for one site's card on the admin overview."""
    from . import scheduler
    since30 = utcnow() - timedelta(days=30)
    since1 = utcnow() - timedelta(days=1)
    out: dict = {"db_bytes": _db_size(site), "scraping": scheduler.is_scraping(site)}
    try:
        with tenancy.use(site), session_scope() as s:
            profile = load_profile(s)
            last_ok = s.scalar(select(func.max(ScrapeRun.finished_at)).where(ScrapeRun.ok.is_(True)))
            last_any = s.scalar(select(ScrapeRun).order_by(ScrapeRun.id.desc()).limit(1))
            ai = s.execute(select(func.count(), func.coalesce(func.sum(AIUsage.cost_usd), 0))
                           .where(AIUsage.at >= since30)).one()
            out.update({
                "profile_name": profile.name, "group_size": profile.group_size, "budget_pppw": profile.budget_pppw,
                "people": [p.name for p in profile.people],
                "listings": s.scalar(select(func.count()).select_from(Listing).where(Listing.site_status == "active",
                                                                                     Listing.duplicate_of_id.is_(None))),
                "passing": s.scalar(select(func.count()).select_from(Listing).where(Listing.passes_filters.is_(True),
                                                                                    Listing.duplicate_of_id.is_(None))),
                "new_24h": s.scalar(select(func.count()).select_from(Listing).where(Listing.first_seen >= since1,
                                                                                    Listing.passes_filters.is_(True))),
                "last_scrape_ok": last_ok.isoformat() if last_ok else None,
                "last_run": {"source": last_any.source, "ok": last_any.ok,
                             "at": (last_any.finished_at or last_any.started_at).isoformat()} if last_any else None,
                "failed_runs_24h": s.scalar(select(func.count()).select_from(ScrapeRun).where(
                    ScrapeRun.started_at >= since1, ScrapeRun.ok.is_(False), ScrapeRun.finished_at.is_not(None))),
                "ai_requests_30d": ai[0], "ai_cost_30d": float(ai[1] or 0),
                "emails_30d": s.scalar(select(func.count()).select_from(EmailLog).where(EmailLog.sent_at >= since30,
                                                                                        EmailLog.ok.is_(True))),
                "email_failures_30d": s.scalar(select(func.count()).select_from(EmailLog).where(
                    EmailLog.sent_at >= since30, EmailLog.ok.is_(False))),
                "recipients": s.scalar(select(func.count()).select_from(Recipient).where(Recipient.active.is_(True))),
            })
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    from .admin_db import RequestLog
    since7 = utcnow() - timedelta(days=7)
    with admin_session() as s:
        r = s.execute(select(func.count(), func.count(func.distinct(RequestLog.viewer)), func.max(RequestLog.at))
                      .where(RequestLog.site == site.slug, RequestLog.at >= since7)).one()
    out.update({"requests_7d": r[0], "viewers_7d": r[1], "last_visit": r[2].isoformat() if r[2] else None})
    return out


def site_dict(row: Site, with_usage: bool = False) -> dict:
    info = tenancy.by_slug(row.slug)
    d = {"slug": row.slug, "name": row.name, "hostname": info.hostname if info else row.hostname,
         "url": info.base_url if info else f"https://{row.hostname}", "is_default": row.is_default,
         "enabled": row.enabled, "strict_auth": row.strict_auth, "admin_emails": row.admin_emails or "",
         "invited": row.emails or [], "access_app_id": row.access_app_id, "has_access": bool(info and info.access_aud),
         "setup": row.setup or {}, "steps": [{"key": k, "label": STEP_LABELS[k]} for k in STEPS],
         "notes": row.notes, "created_at": row.created_at.isoformat() if row.created_at else None}
    if with_usage and info:
        d["usage"] = usage(info)
    return d


def admin_access_aud() -> str | None:
    return get_settings().admin_access_aud or admin_db.get_meta("admin_access_aud")


def setup_admin_site() -> dict:
    """Put the admin site itself online (DNS, tunnel, Access for HF_ADMIN_EMAILS) and remember its AUD."""
    st = get_settings()
    host = tenancy.admin_host()
    admins = sorted(st.admins)
    if not admins:
        raise ValueError("Set HF_ADMIN_EMAILS first: only those people can use the admin site.")
    out = {"dns": cloudflare.ensure_dns(host), "tunnel": cloudflare.ensure_ingress(host)}
    res = cloudflare.ensure_access_app("Admin", host, admins, reuse_policy=st.admin_access_policy or None)
    admin_db.set_meta("admin_access_aud", res["aud"])
    out.update(res)
    out["check"] = cloudflare.check_live(host)
    return out
