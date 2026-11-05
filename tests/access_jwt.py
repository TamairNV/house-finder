"""Sign Cloudflare Access style tokens for tests."""
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEAM = "team.example.com"
AUD = "house-aud"


def token(email, aud=AUD, iss=f"https://{TEAM}", key=KEY, exp=None):
    return jwt.encode({"email": email, "aud": aud, "iss": iss, "exp": exp or time.time() + 600}, key, algorithm="RS256")


def signed_in(email):
    return {"Cf-Access-Jwt-Assertion": token(email)}
