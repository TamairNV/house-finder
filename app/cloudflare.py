"""Put a site on the internet: DNS record, tunnel route and Access login, all through the Cloudflare API.

Uses the same token as the login panel (HF_CF_API_TOKEN). It needs, on the account: Cloudflare Tunnel: Edit and
Access: Apps and Policies: Edit; on the zone: DNS: Edit. Every step is idempotent so "Retry" is always safe.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from . import access
from .access import AccessError, _req
from .config import get_settings

log = logging.getLogger(__name__)


class SetupError(AccessError):
    pass


def _tunnel() -> str:
    t = get_settings().cf_tunnel_id
    if not t:
        raise SetupError("Set HF_CF_TUNNEL_ID (the tunnel that serves this app).")
    return t


def zone_id() -> str:
    st = get_settings()
    if st.cf_zone_id:
        return st.cf_zone_id
    zones = _req("GET", "/zones", params={"name": st.site_domain})["result"]
    if not zones:
        raise SetupError(f"The token can't see the {st.site_domain} zone (needs Zone: DNS: Edit).")
    return zones[0]["id"]


# ---------------------------------------------------------------- DNS

def ensure_dns(hostname: str) -> str:
    """Proxied CNAME hostname -> <tunnel>.cfargotunnel.com. Returns the record id."""
    z = zone_id()
    target = f"{_tunnel()}.cfargotunnel.com"
    existing = _req("GET", f"/zones/{z}/dns_records", params={"name": hostname})["result"]
    for r in existing:
        if r.get("type") == "CNAME" and r.get("content") == target:
            return r["id"]
    if existing:
        kinds = ", ".join(f"{r.get('type')} {r.get('content')}" for r in existing)
        raise SetupError(f"{hostname} already has a DNS record ({kinds}); pick another name or remove it first.")
    r = _req("POST", f"/zones/{z}/dns_records", json={
        "type": "CNAME", "name": hostname, "content": target, "proxied": True, "ttl": 1,
        "comment": "house-finder site (made by the admin site)"})["result"]
    return r["id"]


def remove_dns(hostname: str) -> bool:
    z = zone_id()
    target = f"{_tunnel()}.cfargotunnel.com"
    gone = False
    for r in _req("GET", f"/zones/{z}/dns_records", params={"name": hostname})["result"]:
        if r.get("type") == "CNAME" and r.get("content") == target:  # never delete records we didn't make
            _req("DELETE", f"/zones/{z}/dns_records/{r['id']}")
            gone = True
    return gone


# ---------------------------------------------------------------- tunnel route

def _tunnel_config() -> tuple[str, dict]:
    path = f"/accounts/{access.account_id()}/cfd_tunnel/{_tunnel()}/configurations"
    res = _req("GET", path)["result"] or {}
    return path, res.get("config") or {}


def _backup(config: dict) -> None:
    from . import admin_db
    backups = admin_db.get_meta("tunnel_backups", []) or []
    backups.append({"at": datetime.now(timezone.utc).isoformat(), "config": config})
    admin_db.set_meta("tunnel_backups", backups[-10:])


def ensure_ingress(hostname: str) -> bool:
    """Add hostname -> HF_TUNNEL_SERVICE before the catch-all rule. Returns True if it changed anything."""
    path, config = _tunnel_config()
    ingress = list(config.get("ingress") or [])
    if any(r.get("hostname") == hostname for r in ingress):
        return False
    if not ingress or ingress[-1].get("hostname"):
        raise SetupError("The tunnel config has no catch-all rule at the end; fix it in the dashboard first.")
    _backup(config)
    rule = {"hostname": hostname, "service": get_settings().tunnel_service, "originRequest": {}}
    config = {**config, "ingress": [*ingress[:-1], rule, ingress[-1]]}
    _req("PUT", path, json={"config": config})
    log.info("Tunnel: routed %s", hostname)
    return True


def remove_ingress(hostname: str) -> bool:
    path, config = _tunnel_config()
    ingress = list(config.get("ingress") or [])
    kept = [r for r in ingress if r.get("hostname") != hostname]
    if len(kept) == len(ingress):
        return False
    _backup(config)
    _req("PUT", path, json={"config": {**config, "ingress": kept}})
    return True


# ---------------------------------------------------------------- Access app

def find_app(hostname: str) -> dict | None:
    apps = _req("GET", f"/accounts/{access.account_id()}/access/apps")["result"]
    for a in apps:
        if a.get("domain", "").split("/")[0] == hostname or hostname in (a.get("self_hosted_domains") or []):
            return a
    return None


def find_policy(name: str) -> dict | None:
    pols = _req("GET", f"/accounts/{access.account_id()}/access/policies")["result"]
    return next((p for p in pols if p.get("name") == name), None)


def ensure_access_app(name: str, hostname: str, emails: list[str], reuse_policy: str | None = None) -> dict:
    """Make (or find) the Access app for hostname, with an allow policy listing emails.

    Returns {"app_id", "aud", "policy_id"}.
    """
    acct = access.account_id()
    app = find_app(hostname)
    if app:
        allow = [p for p in app.get("policies") or [] if p.get("decision") == "allow"]
        return {"app_id": app["id"], "aud": app.get("aud"), "policy_id": allow[0]["id"] if allow else None}
    policy = find_policy(reuse_policy) if reuse_policy else None
    if policy is None:
        emails = sorted({e.strip().lower() for e in emails if e and e.strip()})
        if not emails:
            raise SetupError("Add at least one email so someone can log in.")
        policy = _req("POST", f"/accounts/{acct}/access/policies", json={
            "name": f"House finder · {name}", "decision": "allow",
            "include": [{"email": {"email": e}} for e in emails],
            "exclude": [], "require": [], "session_duration": get_settings().access_session})["result"]
    app = _req("POST", f"/accounts/{acct}/access/apps", json={
        "name": f"House finder · {name}", "domain": hostname, "type": "self_hosted",
        "session_duration": get_settings().access_session, "app_launcher_visible": False,
        "auto_redirect_to_identity": False, "policies": [{"id": policy["id"], "precedence": 1}]})["result"]
    log.info("Access: protected %s", hostname)
    return {"app_id": app["id"], "aud": app.get("aud"), "policy_id": policy["id"]}


def remove_access_app(app_id: str | None, policy_id: str | None) -> None:
    acct = access.account_id()
    if app_id:
        try:
            _req("DELETE", f"/accounts/{acct}/access/apps/{app_id}")
        except AccessError as e:
            if "not found" not in str(e).lower():
                raise
    if policy_id:
        try:
            pol = _req("GET", f"/accounts/{acct}/access/policies/{policy_id}")["result"]
            if (pol.get("name") or "").startswith("House finder · ") and not pol.get("app_count"):
                _req("DELETE", f"/accounts/{acct}/access/policies/{policy_id}")
        except AccessError:
            pass  # app-scoped policies go with the app


# ---------------------------------------------------------------- live check

def check_live(hostname: str) -> dict:
    """Is hostname answering through Cloudflare, behind an Access login?"""
    url = f"https://{hostname}/healthz"
    try:
        r = httpx.get(url, follow_redirects=False, timeout=10)
    except httpx.HTTPError as e:
        return {"ok": False, "state": "unreachable", "msg": f"Can't reach {hostname} yet ({type(e).__name__}); DNS may take a minute."}
    loc = r.headers.get("location", "")
    if r.status_code in (301, 302, 303) and "cloudflareaccess.com" in loc:
        return {"ok": True, "state": "protected", "msg": "Up, and asks for a login first."}
    if r.status_code == 200:
        try:
            body = r.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        if body.get("ok"):
            return {"ok": False, "state": "open", "msg": "Up but NOT behind a login. Check the Access app."}
    if r.status_code in (502, 530):
        return {"ok": False, "state": "no-route", "msg": f"Cloudflare can't reach the app (HTTP {r.status_code}); is the tunnel running?"}
    return {"ok": False, "state": f"http-{r.status_code}", "msg": f"Unexpected answer: HTTP {r.status_code}"}
