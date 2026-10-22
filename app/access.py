"""Manage who can log in: the email allow-list of the Cloudflare Access policy in front of the site.

Needs a Cloudflare API token with "Access: Apps and Policies: Edit" (HF_CF_API_TOKEN). The account,
Access app and policy are found automatically from HF_BASE_URL's hostname unless set explicitly.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

import httpx

from .config import get_settings

log = logging.getLogger(__name__)
API = "https://api.cloudflare.com/client/v4"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Fields the API returns but won't accept back on update.
READ_ONLY = {"id", "uid", "created_at", "updated_at", "app_count", "reusable", "app_uid", "precedence"}

_target: dict | None = None  # cached {"account", "app", "policy", "url"}
_target_at = 0.0


class AccessError(Exception):
    pass


def configured() -> bool:
    return bool(get_settings().cf_api_token)


def _req(method: str, path: str, **kw) -> dict:
    st = get_settings()
    try:
        r = httpx.request(method, API + path, headers={"Authorization": f"Bearer {st.cf_api_token}"}, timeout=20, **kw)
    except httpx.HTTPError as e:
        raise AccessError(f"Couldn't reach Cloudflare: {e}") from e
    try:
        body = r.json()
    except ValueError:
        raise AccessError(f"Cloudflare returned HTTP {r.status_code}")
    if not body.get("success"):
        msg = "; ".join(e.get("message", "") for e in body.get("errors") or []) or f"HTTP {r.status_code}"
        raise AccessError(f"Cloudflare: {msg}")
    return body


def _find_target() -> dict:
    """Locate the account, Access app (by hostname) and its allow policy, caching for 10 minutes."""
    global _target, _target_at
    if _target and time.time() - _target_at < 600:
        return _target
    st = get_settings()
    account = st.cf_account_id
    if not account:
        accounts = _req("GET", "/accounts")["result"]
        if not accounts:
            raise AccessError("Set HF_CF_ACCOUNT_ID, or give the token Account Settings: Read so it can find the account.")
        if len(accounts) > 1:
            raise AccessError("Set HF_CF_ACCOUNT_ID: the token can see more than one Cloudflare account.")
        account = accounts[0]["id"]

    app_id, policy_id = st.cf_access_app_id, st.cf_access_policy_id
    app = None
    if not app_id:
        host = urlparse(st.base_url).hostname or ""
        apps = _req("GET", f"/accounts/{account}/access/apps")["result"]
        matches = [a for a in apps if host and (a.get("domain", "").split("/")[0] == host
                                                or host in (a.get("self_hosted_domains") or []))]
        if not matches:
            raise AccessError(f"No Cloudflare Access app found for {host or 'HF_BASE_URL'}.")
        app = matches[0]
        app_id = app["id"]
    if not policy_id:
        app = app or _req("GET", f"/accounts/{account}/access/apps/{app_id}")["result"]
        allow = [p for p in app.get("policies") or [] if p.get("decision") == "allow"]
        if not allow:
            raise AccessError("The Access app has no Allow policy to edit.")
        policy_id = allow[0]["id"]

    # Reusable policies (what the dashboard makes now) live at the account level; older ones under the app.
    for url in (f"/accounts/{account}/access/policies/{policy_id}",
                f"/accounts/{account}/access/apps/{app_id}/policies/{policy_id}"):
        try:
            _req("GET", url)
        except AccessError:
            continue
        _target, _target_at = {"account": account, "app": app_id, "policy": policy_id, "url": url}, time.time()
        return _target
    raise AccessError("Couldn't read the Access policy; check the token has Access: Apps and Policies: Edit.")


def _policy() -> tuple[str, dict]:
    t = _find_target()
    return t["url"], _req("GET", t["url"])["result"]


def _emails(rules: list[dict]) -> list[str]:
    return [r["email"]["email"] for r in rules if isinstance(r.get("email"), dict) and r["email"].get("email")]


def list_emails() -> dict:
    _, pol = _policy()
    other = [r for r in pol.get("include") or [] if "email" not in r]
    return {"emails": sorted(_emails(pol.get("include") or []), key=str.lower), "policy": pol.get("name"),
            "other_rules": len(other)}


def _save(url: str, pol: dict, include: list[dict]) -> None:
    body = {k: v for k, v in pol.items() if k not in READ_ONLY}
    body["include"] = include
    _req("PUT", url, json=body)


def add_email(email: str) -> list[str]:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise AccessError("That doesn't look like an email address.")
    url, pol = _policy()
    include = pol.get("include") or []
    if email in (e.lower() for e in _emails(include)):
        return _emails(include)
    include = [*include, {"email": {"email": email}}]
    _save(url, pol, include)
    return _emails(include)


def remove_email(email: str, protect: set[str] = frozenset()) -> list[str]:
    email = email.strip().lower()
    if email in protect:
        raise AccessError("You can't remove yourself or an admin here.")
    url, pol = _policy()
    include = pol.get("include") or []
    kept = [r for r in include if not (isinstance(r.get("email"), dict) and r["email"].get("email", "").lower() == email)]
    if len(kept) == len(include):
        return _emails(include)
    if not kept:
        raise AccessError("That's the last rule; removing it would lock everyone out.")
    _save(url, pol, kept)
    return _emails(kept)
