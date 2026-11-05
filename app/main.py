from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import admin_db, applog, netstats, photos, scheduler, tenancy
from .api import _can_edit_access, _viewer, router as api_router
from .config import get_settings
from .db import init_db, session_scope
from .models import Listing
from .store import load_profile, seed_from_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("house_finder")

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def asset_version() -> str:
    """Changes whenever a static file changes, so browsers and Cloudflare's cache pick up new CSS/JS."""
    h = hashlib.sha1()
    for f in sorted((HERE / "static").rglob("*.*")):
        st = f.stat()
        h.update(f"{f.name}{st.st_size}{st.st_mtime_ns}".encode())
    return h.hexdigest()[:10]


templates.env.globals["v"] = asset_version()


@asynccontextmanager
async def lifespan(app: FastAPI):
    applog.install()
    admin_db.ensure_default_site()
    with tenancy.use(tenancy.current()):
        init_db()
        with session_scope() as s:
            seed_from_config(s)
    scheduler.init_sites()
    scheduler.start()
    yield
    scheduler.shutdown()
    netstats.flush()


site_app = FastAPI(title="House finder", lifespan=lifespan)
site_app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
site_app.include_router(api_router)
app = site_app  # replaced by the host router at the bottom of this file


@site_app.middleware("http")
async def basic_auth(request: Request, call_next):
    s = get_settings()
    if s.ui_username and s.ui_password and request.url.path != "/healthz":
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, s.ui_username) and secrets.compare_digest(pw, s.ui_password)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="house-finder"'})
    return await call_next(request)


@site_app.get("/healthz")
def healthz():
    return {"ok": True}


def _page(request: Request, name: str, **ctx):
    with session_scope() as s:
        profile = load_profile(s)
    viewer = _viewer(request)
    me = next((p.name for p in profile.people if viewer and (p.email or "").strip().lower() == viewer), None)
    site = tenancy.current()
    admin_url = f"https://{tenancy.admin_host()}" if viewer and viewer in get_settings().admins else None
    return templates.TemplateResponse(request, name, {"profile": profile, "is_admin": _can_edit_access(request),
                                                      "me": me, "site": site, "admin_url": admin_url, **ctx})


@site_app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _page(request, "index.html", page="listings")


@site_app.get("/listing/{listing_id}", response_class=HTMLResponse)
def listing_page(request: Request, listing_id: int):
    with session_scope() as s:
        if s.get(Listing, listing_id) is None:
            raise HTTPException(404)
    return _page(request, "listing.html", page="listing", listing_id=listing_id)


@site_app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _page(request, "settings.html", page="settings")


@site_app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    return _page(request, "status.html", page="status")


# ---------------------------------------------------------------- photo cache

@site_app.get("/img")
def image_proxy(u: str):
    """Fetch-and-cache listing photos so they survive the listing being taken down
    (and so hotlink protection doesn't break the UI)."""
    try:
        data, ctype = photos.fetch(u)
    except photos.PhotoError as e:
        raise HTTPException(e.status, str(e))
    return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------------------------------------------------------------- host router

SKIP_LOG = ("/static/", "/healthz", "/favicon")
OPEN_PATHS = ("/healthz", "/static/")


def _headers(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}


async def _plain(send, status: int, text: str) -> None:
    body = f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>House finder</title><body style="font:16px system-ui;max-width:32rem;margin:15vh auto;padding:0 16px;color:#334">
<h2>🏠 House finder</h2><p>{text}</p></body>""".encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/html; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class HostRouter:
    """Sends each request to the admin app or to the right site (by Host), checks logins, logs traffic."""

    def __init__(self, site, admin):
        self.site, self.admin = site, admin

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.site(scope, receive, send)  # lifespan
        from .access_token import verified_email
        from .sites import admin_access_aud

        h = _headers(scope)
        target = tenancy.resolve(h.get("host"))
        path = scope.get("path") or "/"
        token = h.get("cf-access-jwt-assertion")
        started = time.monotonic()
        meta = {"status": 500, "bytes": 0}

        async def counting_send(msg):
            if msg["type"] == "http.response.start":
                meta["status"] = msg["status"]
            elif msg["type"] == "http.response.body":
                meta["bytes"] += len(msg.get("body") or b"")
            await send(msg)

        open_path = path == "/healthz" or path.startswith("/static/")
        viewer = None
        slug = "?"
        try:
            if target is None:
                slug = "-"
                return await _plain(counting_send, 404, "There's no house finder site at this address.")
            if target == "admin":
                slug = "admin"
                aud = admin_access_aud()
                viewer = verified_email(token, aud) if aud else None
                admins = get_settings().admins
                if not open_path and not (viewer and viewer in admins):
                    why = "the admin login isn't set up yet (run <code>python -m app.cli setup-admin</code>)." if not aud \
                        else "this page is only for the admin."
                    return await _plain(counting_send, 403, f"Sorry, {why}")
                scope.setdefault("state", {})["viewer"] = viewer
                return await self.admin(scope, receive, counting_send)
            slug = target.slug
            viewer = verified_email(token, target.access_aud) if target.access_aud else None
            if target.strict_auth and not open_path and viewer is None:
                return await _plain(counting_send, 403, "Please open this site through its normal link so you can log in.")
            with tenancy.use(target):
                return await self.site(scope, receive, counting_send)
        finally:
            if get_settings().log_requests and not path.startswith(SKIP_LOG) and scope["type"] == "http":
                netstats.record_request(site=slug, method=scope.get("method", "GET"), path=path, status=meta["status"],
                                        ms=int((time.monotonic() - started) * 1000), bytes_out=meta["bytes"],
                                        viewer=viewer, country=h.get("cf-ipcountry"),
                                        via="cloudflare" if "cf-ray" in h else "direct")


from .admin import admin_app  # noqa: E402

app = HostRouter(site_app, admin_app)
