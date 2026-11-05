"""Verifies the Cloudflare Access login token. The plain email header can be faked by anyone who reaches the port directly."""
from __future__ import annotations

from functools import lru_cache

import jwt

from .config import get_settings


@lru_cache(maxsize=1)
def _jwks(team_domain: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs", cache_keys=True)


def _signing_key(token: str):
    return _jwks(get_settings().cf_team_domain).get_signing_key_from_jwt(token).key


def verified_email(token: str | None) -> str | None:
    s = get_settings()
    if not (token and s.cf_team_domain and s.cf_access_aud):
        return None
    try:
        claims = jwt.decode(token, _signing_key(token), algorithms=["RS256"], audience=s.cf_access_aud,
                            issuer=f"https://{s.cf_team_domain}")
    except Exception:
        return None
    email = (claims.get("email") or "").strip().lower()
    return email or None
