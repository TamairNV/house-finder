from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import scheduler
from .api import router as api_router
from .config import get_settings
from .db import init_db, session_scope
from .models import Listing
from .store import load_profile, seed_from_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("house_finder")

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session_scope() as s:
        seed_from_config(s)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="House finder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(api_router)


@app.middleware("http")
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


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _page(request: Request, name: str, **ctx):
    with session_scope() as s:
        profile = load_profile(s)
    return templates.TemplateResponse(request, name, {"profile": profile, **ctx})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _page(request, "index.html", page="listings")


@app.get("/listing/{listing_id}", response_class=HTMLResponse)
def listing_page(request: Request, listing_id: int):
    with session_scope() as s:
        if s.get(Listing, listing_id) is None:
            raise HTTPException(404)
    return _page(request, "listing.html", page="listing", listing_id=listing_id)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _page(request, "settings.html", page="settings")


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    return _page(request, "status.html", page="status")


# ---------------------------------------------------------------- photo cache

ALLOWED_IMG_HOSTS = ("rightmove.co.uk", "zoocdn.com", "zoopla.co.uk", "openrent.co.uk", "spareroom.co.uk",
                     "sturents.com", "accommodationforstudents.com", "unihomes.co.uk", "cloudfront.net",
                     "amazonaws.com", "imgix.net", "picsum.photos")


@app.get("/img")
def image_proxy(u: str):
    """Fetch-and-cache listing photos so they survive the listing being taken down
    (and so hotlink protection doesn't break the UI)."""
    host = httpx.URL(u).host if u.startswith("http") else ""
    if not any(host.endswith(h) for h in ALLOWED_IMG_HOSTS):
        raise HTTPException(400, "host not allowed")
    s = get_settings()
    cache_dir = s.data_dir / "photos"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(u.encode()).hexdigest()
    path = cache_dir / key[:2] / key
    if path.exists():
        return FileResponse(path, media_type=(path.with_suffix(".type").read_text() if path.with_suffix(".type").exists() else "image/jpeg"),
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})
    try:
        r = httpx.get(u, headers={"User-Agent": s.user_agent, "Referer": f"https://{host}/"}, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502, "upstream image fetch failed")
    ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
    if not ctype.startswith("image/"):
        raise HTTPException(502, "not an image")
    if s.cache_photos:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        path.with_suffix(".type").write_text(ctype)
    return Response(r.content, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})
