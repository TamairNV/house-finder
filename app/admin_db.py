"""The admin database (data/admin.db): the list of sites plus traffic logs shared by all of them.

Kept apart from the per-site databases so a site can be archived by moving one folder.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, create_engine, delete, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings
from .models import utcnow


class AdminBase(DeclarativeBase):
    pass


class Site(AdminBase):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    hostname: Mapped[str] = mapped_column(String(200), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    strict_auth: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_emails: Mapped[str] = mapped_column(String(500), default="")  # site owners (can edit its login list)
    emails: Mapped[list] = mapped_column(JSON, default=list)  # who was invited at setup
    access_app_id: Mapped[str | None] = mapped_column(String(64))
    access_policy_id: Mapped[str | None] = mapped_column(String(64))
    access_aud: Mapped[str | None] = mapped_column(String(100))
    dns_record_id: Mapped[str | None] = mapped_column(String(64))
    setup: Mapped[dict] = mapped_column(JSON, default=dict)  # {step: {"ok": bool, "msg": str, "at": iso}}
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RequestLog(AdminBase):
    """One request to any site (static files left out)."""
    __tablename__ = "request_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    site: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    status: Mapped[int] = mapped_column(Integer)
    ms: Mapped[int] = mapped_column(Integer)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)
    viewer: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(4))
    via: Mapped[str] = mapped_column(String(12), default="cloudflare")  # cloudflare | direct


class FetchLog(AdminBase):
    """One outgoing scraper request."""
    __tablename__ = "fetch_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    site: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(120))
    status: Mapped[int] = mapped_column(Integer)  # 0 = network error
    ms: Mapped[int] = mapped_column(Integer)
    bytes_in: Mapped[int] = mapped_column(Integer, default=0)


class AdminMeta(AdminBase):
    __tablename__ = "admin_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


_engines: dict[str, tuple] = {}
_lock = threading.Lock()


def admin_db_url() -> str:
    st = get_settings()
    return st.admin_database_url or f"sqlite:///{(st.data_dir / 'admin.db').as_posix()}"


def _factory() -> sessionmaker[Session]:
    url = admin_db_url()
    hit = _engines.get(url)
    if hit:
        return hit[1]
    with _lock:
        if url in _engines:
            return _engines[url][1]
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        AdminBase.metadata.create_all(eng)
        from .db import add_missing_columns
        add_missing_columns(eng, AdminBase)
        sm = sessionmaker(bind=eng, expire_on_commit=False)
        _engines[url] = (eng, sm)
        return sm


def reset() -> None:
    for eng, _ in _engines.values():
        eng.dispose()
    _engines.clear()


@contextmanager
def admin_session() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def list_site_rows() -> list[Site]:
    with admin_session() as s:
        return list(s.scalars(select(Site).order_by(Site.id)))


def ensure_default_site() -> None:
    """The original site gets a row so it can be renamed and given owners like the others."""
    from . import tenancy
    d = tenancy.default_site()
    with admin_session() as s:
        if s.scalar(select(Site).where(Site.is_default.is_(True))) is None:
            s.add(Site(slug=d.slug, name="House finder", hostname=d.hostname or "localhost", is_default=True,
                       strict_auth=False, setup={"existing": {"ok": True, "msg": "Original site", "at": utcnow().isoformat()}}))
    tenancy.invalidate()


def get_meta(key: str, default=None):
    with admin_session() as s:
        row = s.get(AdminMeta, key)
        return row.value if row else default


def set_meta(key: str, value) -> None:
    with admin_session() as s:
        row = s.get(AdminMeta, key)
        if row:
            row.value = value
        else:
            s.add(AdminMeta(key=key, value=value))


def prune(days: int = 30) -> None:
    cutoff = utcnow() - timedelta(days=days)
    with admin_session() as s:
        s.execute(delete(RequestLog).where(RequestLog.at < cutoff))
        s.execute(delete(FetchLog).where(FetchLog.at < cutoff))
