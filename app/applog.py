"""App log for the admin site: the last few thousand log lines in memory, plus a rotating file in data/logs."""
from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from datetime import datetime, timezone

MAX = 4000
_records: deque[dict] = deque(maxlen=MAX)
_installed = False


class _Ring(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            from .tenancy import _current
            site = _current.get()
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            _records.append({
                "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname, "logger": record.name, "site": site.slug if site else None,
                "msg": msg[:4000],
            })
        except Exception:  # noqa: BLE001
            pass


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    root = logging.getLogger()
    ring = _Ring(level=logging.INFO)
    root.addHandler(ring)
    try:
        from .config import get_settings
        d = get_settings().data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(d / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass


def records(level: str | None = None, site: str | None = None, q: str | None = None, limit: int = 500) -> list[dict]:
    order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    floor = order.get((level or "").upper(), 0)
    out = []
    for r in reversed(_records):
        if order.get(r["level"], 0) < floor:
            continue
        if site and r["site"] != site:
            continue
        if q and q.lower() not in r["msg"].lower() and q.lower() not in r["logger"].lower():
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out
