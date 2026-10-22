import pytest
from fastapi.testclient import TestClient

from app import access
from app.config import get_settings
from app.main import app


@pytest.fixture()
def cf(session, monkeypatch):
    """Fake Cloudflare API holding one Access app with a reusable allow policy."""
    st = get_settings()
    monkeypatch.setattr(st, "cf_api_token", "t")
    monkeypatch.setattr(st, "base_url", "https://house.example.co.uk")
    monkeypatch.setattr(st, "admin_emails", "boss@example.com")
    monkeypatch.setattr(access, "_target", None)
    policy = {"id": "pol1", "name": "Friends", "decision": "allow", "created_at": "x", "reusable": True,
              "include": [{"email": {"email": "boss@example.com"}}, {"email_domain": {"domain": "uni.ac.uk"}}],
              "exclude": [], "require": []}
    puts = []

    def fake_req(method, path, **kw):
        if path == "/accounts":
            return {"result": [{"id": "acc"}]}
        if path == "/accounts/acc/access/apps":
            return {"result": [{"id": "other", "domain": "x.example.com", "policies": []},
                               {"id": "app1", "domain": "house.example.co.uk", "policies": [{"id": "pol1", "decision": "allow"}]}]}
        if path == "/accounts/acc/access/policies/pol1":
            if method == "PUT":
                assert "created_at" not in kw["json"] and "id" not in kw["json"]
                puts.append(kw["json"])
                policy["include"] = kw["json"]["include"]
            return {"result": dict(policy)}
        raise access.AccessError("not found")

    monkeypatch.setattr(access, "_req", fake_req)
    return puts


def test_list_add_remove(cf):
    boss = {"cf-access-authenticated-user-email": "Boss@example.com"}
    with TestClient(app) as c:
        a = c.get("/api/access", headers=boss).json()
        assert a["emails"] == ["boss@example.com"] and a["can_edit"] and a["me"] == "boss@example.com" and a["other_rules"] == 1
        assert c.post("/api/access", json={"email": "Mate@Example.com "}, headers=boss).json()["emails"] == ["boss@example.com", "mate@example.com"]
        assert c.post("/api/access", json={"email": "mate@example.com"}, headers=boss).status_code == 200  # no duplicate
        assert len(cf) == 1 and cf[0]["name"] == "Friends" and cf[0]["include"][1] == {"email_domain": {"domain": "uni.ac.uk"}}
        assert c.post("/api/access", json={"email": "nope"}, headers=boss).status_code == 400
        assert c.delete("/api/access/boss@example.com", headers=boss).status_code == 400  # can't remove an admin/yourself
        assert c.delete("/api/access/mate@example.com", headers=boss).json()["emails"] == ["boss@example.com"]


def test_only_admins_can_edit(cf):
    mate = {"cf-access-authenticated-user-email": "mate@example.com"}
    with TestClient(app) as c:
        assert c.get("/api/access", headers=mate).json()["can_edit"] is False
        assert c.post("/api/access", json={"email": "x@example.com"}, headers=mate).status_code == 403
        assert c.post("/api/access", json={"email": "x@example.com"}).status_code == 403  # no header (e.g. LAN) when admins are set
        assert not cf


def test_not_configured(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "cf_api_token", None)
    with TestClient(app) as c:
        a = c.get("/api/access").json()
        assert a["configured"] is False and a["emails"] == []


def test_no_matching_app(cf, monkeypatch):
    monkeypatch.setattr(get_settings(), "base_url", "https://elsewhere.example.com")
    with TestClient(app) as c:
        assert "No Cloudflare Access app" in c.get("/api/access").json()["error"]
