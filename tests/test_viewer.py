import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.access_jwt import AUD, OTHER_KEY, TEAM, signed_in, token

BOSS = "boss@example.com"


@pytest.fixture()
def client(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", BOSS)
    return TestClient(app)


def can_edit(client, headers):
    return client.get("/api/access", headers=headers).json()["can_edit"]


def test_faked_email_header_is_not_believed(client):
    assert can_edit(client, {"Cf-Access-Authenticated-User-Email": BOSS}) is False


def test_verified_token_is_believed_and_case_ignored(client):
    assert can_edit(client, signed_in(BOSS.upper())) is True


def test_non_admin_token_cannot_edit(client):
    assert can_edit(client, signed_in("mate@example.com")) is False


@pytest.mark.parametrize("bad", [
    lambda: token(BOSS, aud="other"), lambda: token(BOSS, iss="https://evil"),
    lambda: token(BOSS, key=OTHER_KEY), lambda: token(BOSS, exp=time.time() - 10), lambda: "junk"])
def test_bad_tokens_cannot_edit(client, bad):
    assert can_edit(client, {"Cf-Access-Jwt-Assertion": bad()}) is False


def test_no_token_means_no_viewer(client):
    assert client.get("/api/access").json()["me"] is None


def test_unconfigured_never_believes_a_token(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "cf_access_aud", None)
    assert can_edit(client, signed_in(BOSS)) is False


def test_no_admin_list_still_needs_a_real_login(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "")
    c = TestClient(app)
    assert can_edit(c, {}) is False
    assert can_edit(c, signed_in("mate@example.com")) is True


def test_panel_managed_turns_off_the_built_in_friends_editor(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cf_api_token", "t")
    monkeypatch.setattr(s, "access_managed_by_panel", True)
    r = client.get("/api/access", headers=signed_in(BOSS))
    assert r.json()["configured"] is False
    assert client.post("/api/access", json={"email": "x@example.com"}, headers=signed_in(BOSS)).status_code >= 400
    assert client.delete("/api/access/x@example.com", headers=signed_in(BOSS)).status_code >= 400
