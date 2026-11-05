from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engines: dict[str, tuple] = {}  # db url -> (engine, sessionmaker); one per site
_lock = threading.Lock()


def _url() -> str:
    from .tenancy import current
    return current().db_url


def _get(url: str | None = None) -> tuple:
    url = url or _url()
    hit = _engines.get(url)
    if hit:
        return hit
    with _lock:
        if url in _engines:
            return _engines[url]
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(engine, "connect")
            def _pragmas(dbapi_conn, _):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        _engines[url] = (engine, sessionmaker(bind=engine, expire_on_commit=False))
        return _engines[url]


def get_engine():
    """Engine for the current site's database (see tenancy.current)."""
    return _get()[0]


def reset_engine(url: str | None = None) -> None:
    """Tests use this to point at a temporary database."""
    for engine, _ in _engines.values():
        engine.dispose()
    _engines.clear()
    from . import admin_db, tenancy
    admin_db.reset()
    tenancy.invalidate()
    if url:
        get_settings().database_url = url


def init_db() -> None:
    """Create/upgrade the current site's tables."""
    from . import models  # noqa: F401  (register tables)
    engine = get_engine()
    Base.metadata.create_all(engine)
    add_missing_columns(engine, Base)


def add_missing_columns(engine, base=None) -> None:
    """Poor man's migrations: add columns that exist on the models but not in the DB.

    Enough for additive changes (new nullable/defaulted columns). Anything else
    (renames, type changes) needs a manual migration; note it in PLAN.md.
    """
    from sqlalchemy import inspect, text

    base = base or Base
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v)}"
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}{default}'))


@contextmanager
def session_scope() -> Iterator[Session]:
    s = _get()[1]()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
