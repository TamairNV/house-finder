"""Traffic numbers for the admin network page: requests to our sites, scraper requests out, interface bytes.

Rows are buffered and written to the admin database by a small background thread so a page view never waits
on a log write.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import case, func, select

from .models import utcnow

log = logging.getLogger(__name__)

_buf: list = []
_lock = threading.Lock()
_flusher: threading.Thread | None = None
FLUSH_SECONDS = 5


def _start_flusher() -> None:
    global _flusher
    if _flusher is None or not _flusher.is_alive():
        _flusher = threading.Thread(target=_loop, daemon=True, name="netstats-flush")
        _flusher.start()


def _loop() -> None:
    while True:
        time.sleep(FLUSH_SECONDS)
        try:
            flush()
        except Exception:  # noqa: BLE001
            log.exception("netstats flush failed")


def _push(row) -> None:
    with _lock:
        _buf.append(row)
        if len(_buf) > 5000:  # the DB is stuck; drop the oldest rather than grow forever
            del _buf[:1000]
    _start_flusher()


def flush() -> int:
    from .admin_db import admin_session
    with _lock:
        rows, _buf[:] = list(_buf), []
    if rows:
        with admin_session() as s:
            s.add_all(rows)
    return len(rows)


def record_request(*, site: str, method: str, path: str, status: int, ms: int, bytes_out: int,
                   viewer: str | None, country: str | None, via: str) -> None:
    from .admin_db import RequestLog
    _push(RequestLog(at=utcnow(), site=site, method=method[:8], path=path[:200], status=status, ms=ms,
                     bytes_out=bytes_out, viewer=viewer, country=(country or None) and country[:4], via=via))


def record_fetch(*, source: str, host: str, status: int, ms: int, bytes_in: int) -> None:
    try:
        from .admin_db import FetchLog
        from .tenancy import current
        _push(FetchLog(at=utcnow(), site=current().slug, source=source, host=host[:120], status=status, ms=ms,
                       bytes_in=bytes_in))
    except Exception:  # noqa: BLE001  (stats must never break a scrape)
        pass


# ---------------------------------------------------------------- reading


def _bucket(hours: int) -> str:
    """Hourly buckets for up to 2 days, daily beyond."""
    return "%Y-%m-%d %H:00" if hours <= 48 else "%Y-%m-%d"


def summary(hours: int = 24) -> dict:
    from .admin_db import FetchLog, RequestLog, admin_session
    flush()
    since = utcnow() - timedelta(hours=hours)
    fmt = _bucket(hours)
    with admin_session() as s:
        R = RequestLog
        tot = s.execute(select(func.count(), func.coalesce(func.sum(R.bytes_out), 0), func.avg(R.ms),
                               func.count(func.distinct(R.viewer)),
                               func.sum(case((R.status >= 500, 1), else_=0)),
                               func.sum(case(((R.status >= 400) & (R.status < 500), 1), else_=0)))
                        .where(R.at >= since)).one()
        mss = [m for (m,) in s.execute(select(R.ms).where(R.at >= since).order_by(R.ms))]
        by_site = s.execute(select(R.site, func.count(), func.coalesce(func.sum(R.bytes_out), 0), func.avg(R.ms),
                                   func.count(func.distinct(R.viewer)), func.max(R.at))
                            .where(R.at >= since).group_by(R.site).order_by(func.count().desc())).all()
        series = s.execute(select(func.strftime(fmt, R.at), func.count(), func.coalesce(func.sum(R.bytes_out), 0))
                           .where(R.at >= since).group_by(func.strftime(fmt, R.at))).all()
        klass = R.status - R.status % 100
        statuses = s.execute(select(klass, func.count()).where(R.at >= since).group_by(klass)).all()
        paths = s.execute(select(R.site, R.path, func.count(), func.avg(R.ms)).where(R.at >= since)
                          .group_by(R.site, R.path).order_by(func.count().desc()).limit(15)).all()
        viewers = s.execute(select(R.viewer, R.site, func.count(), func.max(R.at)).where(R.at >= since, R.viewer.is_not(None))
                            .group_by(R.viewer, R.site).order_by(func.count().desc()).limit(30)).all()
        countries = s.execute(select(R.country, func.count()).where(R.at >= since, R.country.is_not(None))
                              .group_by(R.country).order_by(func.count().desc()).limit(10)).all()
        via = dict(s.execute(select(R.via, func.count()).where(R.at >= since).group_by(R.via)).all())
        slow = s.execute(select(R.at, R.site, R.method, R.path, R.status, R.ms).where(R.at >= since)
                         .order_by(R.ms.desc()).limit(10)).all()
        errors = s.execute(select(R.at, R.site, R.method, R.path, R.status, R.viewer).where(R.at >= since, R.status >= 400)
                           .order_by(R.id.desc()).limit(25)).all()

        F = FetchLog
        out_tot = s.execute(select(func.count(), func.coalesce(func.sum(F.bytes_in), 0), func.avg(F.ms))
                            .where(F.at >= since)).one()
        out_hosts = s.execute(select(F.host, F.source, func.count(), func.coalesce(func.sum(F.bytes_in), 0), func.avg(F.ms),
                                     func.sum(case((F.status == 0, 1), else_=0)),
                                     func.sum(case((F.status.in_((429, 503)), 1), else_=0)),
                                     func.sum(case((F.status == 403, 1), else_=0)),
                                     func.sum(case(((F.status >= 400) & (F.status != 429) & (F.status != 403), 1), else_=0)))
                              .where(F.at >= since).group_by(F.host, F.source).order_by(func.count().desc())).all()
        out_series = s.execute(select(func.strftime(fmt, F.at), func.count(), func.coalesce(func.sum(F.bytes_in), 0))
                               .where(F.at >= since).group_by(func.strftime(fmt, F.at))).all()
        out_sites = s.execute(select(F.site, func.count(), func.coalesce(func.sum(F.bytes_in), 0))
                              .where(F.at >= since).group_by(F.site)).all()

    def filled(rows) -> list[dict]:
        """Every bucket in the window, empty ones included, so the chart's x axis is real time."""
        have = {t: (n, int(b)) for t, n, b in rows}
        step = timedelta(hours=1) if hours <= 48 else timedelta(days=1)
        t = since.replace(minute=0, second=0, microsecond=0)
        if step.days:
            t = t.replace(hour=0)
        out = []
        while t <= utcnow():
            key = t.strftime(fmt)
            n, b = have.get(key, (0, 0))
            out.append({"t": key, "requests": n, "bytes": b})
            t += step
        return out

    def pct(p: float) -> int | None:
        return mss[min(len(mss) - 1, int(len(mss) * p))] if mss else None

    return {
        "hours": hours, "bucket": "hour" if hours <= 48 else "day",
        "inbound": {
            "requests": tot[0], "bytes": int(tot[1]), "avg_ms": round(tot[2] or 0), "p50_ms": pct(0.5), "p95_ms": pct(0.95),
            "viewers": tot[3], "errors_5xx": int(tot[4] or 0), "errors_4xx": int(tot[5] or 0), "via": via,
            "by_site": [{"site": a, "requests": b, "bytes": int(c), "avg_ms": round(d or 0), "viewers": e,
                         "last": f.isoformat() if f else None} for a, b, c, d, e, f in by_site],
            "series": filled(series),
            "statuses": {str(int(k)): v for k, v in statuses},
            "top_paths": [{"site": a, "path": b, "requests": c, "avg_ms": round(d or 0)} for a, b, c, d in paths],
            "viewers_list": [{"viewer": a, "site": b, "requests": c, "last": d.isoformat()} for a, b, c, d in viewers],
            "countries": [{"country": a, "requests": b} for a, b in countries],
            "slowest": [{"at": a.isoformat(), "site": b, "method": c, "path": d, "status": e, "ms": f} for a, b, c, d, e, f in slow],
            "errors": [{"at": a.isoformat(), "site": b, "method": c, "path": d, "status": e, "viewer": f}
                       for a, b, c, d, e, f in errors],
        },
        "outbound": {
            "requests": out_tot[0], "bytes": int(out_tot[1]), "avg_ms": round(out_tot[2] or 0),
            "hosts": [{"host": h, "source": src, "requests": n, "bytes": int(b), "avg_ms": round(ms or 0),
                       "network_errors": int(ne or 0), "rate_limited": int(rl or 0), "blocked": int(bl or 0),
                       "other_errors": int(oe or 0)} for h, src, n, b, ms, ne, rl, bl, oe in out_hosts],
            "series": filled(out_series),
            "by_site": [{"site": a, "requests": b, "bytes": int(c)} for a, b, c in out_sites],
        },
        "interfaces": interfaces(),
    }


_last_if: tuple[float, dict] | None = None


def interfaces(path: Path = Path("/proc/net/dev")) -> list[dict]:
    """Bytes in/out per network interface since boot, plus the rate since the last call."""
    global _last_if
    try:
        lines = path.read_text().splitlines()[2:]
    except OSError:
        return []
    now = time.monotonic()
    cur: dict[str, tuple[int, int]] = {}
    for line in lines:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9:
            cur[name.strip()] = (int(f[0]), int(f[8]))
    prev = _last_if
    _last_if = (now, cur)
    out = []
    for name, (rx, tx) in cur.items():
        if name == "lo" or rx + tx == 0:
            continue
        d = {"name": name, "rx_bytes": rx, "tx_bytes": tx, "rx_rate": None, "tx_rate": None}
        if prev and name in prev[1] and now - prev[0] > 0.5:
            dt = now - prev[0]
            d["rx_rate"] = max(0, (rx - prev[1][name][0]) / dt)
            d["tx_rate"] = max(0, (tx - prev[1][name][1]) / dt)
        out.append(d)
    return out

