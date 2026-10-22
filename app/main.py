from __future__ import annotations

import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import photos, scheduler
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

@app.get("/img")
def image_proxy(u: str):
    """Fetch-and-cache listing photos so they survive the listing being taken down
    (and so hotlink protection doesn't break the UI)."""
    try:
        data, ctype = photos.fetch(u)
    except photos.PhotoError as e:
        raise HTTPException(e.status, str(e))
    return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})
