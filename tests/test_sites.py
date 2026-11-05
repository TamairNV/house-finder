"""Several sites in one app: host routing, separate databases, logins, the admin site and its setup button."""
import pytest
from fastapi.testclient import TestClient

from app import access, cloudflare, netstats, sites, tenancy
from app.config import get_settings
from app.main import app
from tests.access_jwt import token

ADMIN = "boss@example.com"
ADMIN_HOST = "house-admin.tamairnv.co.uk"


@pytest.fixture()
def env(session, monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "admin_emails", ADMIN)
    monkeypatch.setattr(st, "admin_access_aud", "admin-aud")
    monkeypatch.setattr(st, "base_url", "https://house.example.co.uk")
    monkeypatch.setattr(st, "cf_api_token", None)  # setup steps stay offline unless a test fakes Cloudflare
    tenancy.invalidate()
    return st


def admin_headers(email=ADMIN):
    return {"host": ADMIN_HOST, "Cf-Access-Jwt-Assertion": token(email, aud="admin-aud")}


def site_headers(host, email, aud):
    return {"host": host, "Cf-Access-Jwt-Assertion": token(email, aud=aud)}


def make_site(c, slug="zach", people=None):
    body = {"name": "Zach's hunt", "slug": slug, "group_size": 4,
            "people": people or [{"email": "Zach@Example.com", "name": "Zach", "digest": True, "owner": True}]}
    r = c.post("/api/sites", json=body, headers=admin_headers())
    assert r.status_code == 200, r.text
    return r.json()


def test_admin_needs_admin_login(env):
    with TestClient(app) as c:
        assert c.get("/", headers={"host": ADMIN_HOST}).status_code == 403
        assert c.get("/api/overview", headers=admin_headers("mate@example.com")).status_code == 403
        # a valid login for the house site is not an admin login
        assert c.get("/api/overview", headers={"host": ADMIN_HOST, "Cf-Access-Jwt-Assertion": token(ADMIN)}).status_code == 403
        assert c.get("/healthz", headers={"host": ADMIN_HOST}).status_code == 200
        ok = c.get("/api/overview", headers=admin_headers())
        assert ok.status_code == 200 and ok.json()["sites"][0]["is_default"]
        assert "House admin" in c.get("/", headers=admin_headers()).text


def test_admin_locked_until_set_up(env, monkeypatch):
    monkeypatch.setattr(env, "admin_access_aud", None)
    with TestClient(app) as c:
        r = c.get("/", headers=admin_headers())
        assert r.status_code == 403 and "setup-admin" in r.text


def test_create_site_keeps_data_apart(env):
    with TestClient(app) as c:
        s = make_site(c)
        assert s["hostname"] == "zach.tamairnv.co.uk" and s["setup"]["database"]["ok"] is True
        assert s["setup"]["dns"]["ok"] is False  # no Cloudflare token in tests
        assert c.post("/api/sites", json={"name": "x", "slug": "zach"}, headers=admin_headers()).status_code == 400
        assert c.post("/api/sites", json={"name": "x", "slug": "Bad Name!"}, headers=admin_headers()).status_code == 422
        assert c.get("/api/check-slug?slug=admin", headers=admin_headers()).json()["ok"] is False

        # The new site has its own profile, people and recipients
        sites._save("zach", access_aud="zach-aud")
        zach = site_headers("zach.tamairnv.co.uk", "zach@example.com", "zach-aud")
        prof = c.get("/api/profile", headers=zach).json()["profile"]
        assert prof["name"] == "Zach's hunt" and prof["group_size"] == 4 and prof["people"][0]["email"] == "zach@example.com"
        recips = c.get("/api/recipients", headers=zach).json()
        assert [r["email"] for r in recips] == ["zach@example.com"] and recips[0]["instant_great"] is False
        # ...and the original site is untouched
        assert c.get("/api/recipients").json() == []
        assert c.get("/api/profile").json()["profile"]["name"] != "Zach's hunt"

        # Owners can edit their own site's login list; the house site's admins list doesn't leak across
        assert c.get("/api/access", headers=zach).json()["can_edit"] is True


def test_new_site_needs_its_own_login(env):
    with TestClient(app) as c:
        make_site(c)
        host = {"host": "zach.tamairnv.co.uk"}
        assert c.get("/", headers=host).status_code == 403  # no Access app yet -> nobody gets in
        sites._save("zach", access_aud="zach-aud")
        assert c.get("/api/listings", headers=host).status_code == 403
        # a house login doesn't open Zach's site
        assert c.get("/api/listings", headers={**host, "Cf-Access-Jwt-Assertion": token("zach@example.com")}).status_code == 403
        assert c.get("/api/listings", headers=site_headers("zach.tamairnv.co.uk", "zach@example.com", "zach-aud")).status_code == 200
        assert c.get("/healthz", headers=host).status_code == 200
        # unknown and disabled hosts get nothing
        assert c.get("/", headers={"host": "nobody.tamairnv.co.uk"}).status_code == 404
        assert c.patch("/api/sites/zach", json={"enabled": False}, headers=admin_headers()).status_code == 200
        assert c.get("/api/listings", headers=site_headers("zach.tamairnv.co.uk", "zach@example.com", "zach-aud")).status_code == 404


def test_lan_and_localhost_still_reach_the_original(env):
    with TestClient(app) as c:
        for host in ("192.168.1.199:8095", "localhost:8095", "100.126.112.62:8095", "house.example.co.uk"):
            assert c.get("/api/meta", headers={"host": host}).status_code == 200, host


def test_delete_archives_database(env):
    with TestClient(app) as c:
        make_site(c)
        assert c.delete("/api/sites/zach", headers=admin_headers()).status_code == 400  # needs confirm
        r = c.delete("/api/sites/zach?confirm=zach", headers=admin_headers())
        assert r.status_code == 200 and any("archived" in d for d in r.json()["done"])
        assert not (env.data_dir / "sites" / "zach").exists()
        assert list((env.data_dir / "sites" / "_deleted").iterdir())
        assert c.delete("/api/sites/house?confirm=house", headers=admin_headers()).status_code == 400


class FakeCF:
    """Just enough of the Cloudflare API for the setup steps."""

    def __init__(self):
        self.dns, self.apps, self.policies = [], [], []
        self.ingress = [{"hostname": "house.example.co.uk", "service": "http://localhost:8095"}, {"service": "http_status:404"}]

    def __call__(self, method, path, **kw):
        if path == "/accounts":
            return {"result": [{"id": "acc"}]}
        if path == "/zones":
            return {"result": [{"id": "zone"}]}
        if path == "/zones/zone/dns_records":
            if method == "GET":
                return {"result": [r for r in self.dns if r["name"] == kw["params"]["name"]]}
            self.dns.append({**kw["json"], "id": f"dns{len(self.dns)}"})
            return {"result": self.dns[-1]}
        if path.startswith("/zones/zone/dns_records/"):
            self.dns = [r for r in self.dns if r["id"] != path.rsplit("/", 1)[1]]
            return {"result": {}}
        if path.endswith("/configurations"):
            if method == "PUT":
                self.ingress = kw["json"]["config"]["ingress"]
            return {"result": {"config": {"ingress": self.ingress}}}
        if path == "/accounts/acc/access/policies":
            if method == "GET":
                return {"result": self.policies}
            self.policies.append({**kw["json"], "id": f"pol{len(self.policies)}"})
            return {"result": self.policies[-1]}
        if path.startswith("/accounts/acc/access/policies/"):
            pid = path.rsplit("/", 1)[1]
            pol = next(p for p in self.policies if p["id"] == pid)
            if method == "PUT":
                pol.update(kw["json"])
            if method == "DELETE":
                self.policies.remove(pol)
            return {"result": dict(pol)}
        if path == "/accounts/acc/access/apps":
            if method == "GET":
                return {"result": self.apps}
            app_ = {**kw["json"], "id": f"app{len(self.apps)}", "aud": f"aud{len(self.apps)}",
                    "policies": [{"id": p["id"], "decision": "allow"} for p in kw["json"]["policies"]]}
            self.apps.append(app_)
            return {"result": app_}
        if path.startswith("/accounts/acc/access/apps/"):
            self.apps = [a for a in self.apps if a["id"] != path.rsplit("/", 1)[1]]
            return {"result": {}}
        raise access.AccessError(f"unexpected {method} {path}")


def test_setup_button_puts_site_online(env, monkeypatch):
    fake = FakeCF()
    monkeypatch.setattr(env, "cf_api_token", "t")
    monkeypatch.setattr(access, "_req", fake)
    monkeypatch.setattr(cloudflare, "_req", fake)
    monkeypatch.setattr(access, "_account", None)
    monkeypatch.setattr(access, "_target", None)
    monkeypatch.setattr(sites, "run_setup_async", lambda slug, inc=True: None)
    monkeypatch.setattr(cloudflare, "check_live", lambda h: {"ok": True, "state": "protected", "msg": "Up"})
    with TestClient(app) as c:
        make_site(c)
        setup = sites.run_setup("zach")
        assert all(setup[k]["ok"] for k in sites.STEPS), setup
        again = sites.run_setup("zach")  # idempotent
        assert all(again[k]["ok"] for k in sites.STEPS) and again["tunnel"]["msg"] == "Route already there"
        assert [r["name"] for r in fake.dns] == ["zach.tamairnv.co.uk"] and fake.dns[0]["proxied"]
        assert [r.get("hostname") for r in fake.ingress] == ["house.example.co.uk", "zach.tamairnv.co.uk", None]
        assert len(fake.apps) == 1 and fake.apps[0]["domain"] == "zach.tamairnv.co.uk"
        emails = {i["email"]["email"] for i in fake.policies[0]["include"]}
        assert emails == {"zach@example.com", ADMIN}

        # The new site now trusts its own Access app
        zach = site_headers("zach.tamairnv.co.uk", "zach@example.com", "aud0")
        assert c.get("/api/listings", headers=zach).status_code == 200

        # Admin edits the login list
        r = c.post("/api/sites/zach/access", json={"email": "pal@example.com"}, headers=admin_headers())
        assert r.status_code == 200 and "pal@example.com" in r.json()["emails"]
        assert c.delete(f"/api/sites/zach/access/{ADMIN}", headers=admin_headers()).status_code == 400  # not yourself

        r = c.delete("/api/sites/zach?confirm=zach", headers=admin_headers()).json()
        assert not fake.dns and not fake.apps and [x.get("hostname") for x in fake.ingress] == ["house.example.co.uk", None]
        assert not fake.policies, r


def test_dns_never_clobbers_someone_elses_record(env, monkeypatch):
    fake = FakeCF()
    fake.dns.append({"id": "x", "name": "zach.tamairnv.co.uk", "type": "A", "content": "1.2.3.4"})
    monkeypatch.setattr(env, "cf_api_token", "t")
    monkeypatch.setattr(cloudflare, "_req", fake)
    monkeypatch.setattr(access, "_account", "acc")
    with pytest.raises(cloudflare.SetupError):
        cloudflare.ensure_dns("zach.tamairnv.co.uk")
    assert not cloudflare.remove_dns("zach.tamairnv.co.uk") and fake.dns


def test_scheduler_scrapes_each_site(env, monkeypatch):
    from app import scheduler
    seen = []
    monkeypatch.setattr(scheduler, "run_all", lambda profile: seen.append((tenancy.current().slug, profile.name)) or [])
    monkeypatch.setattr(scheduler, "_quiet", lambda p, now: False)
    with TestClient(app) as c:
        make_site(c)
    scheduler.tick()
    assert sorted(s for s, _ in seen) == ["house", "zach"] and ("zach", "Zach's hunt") in seen


def test_network_and_logs(env, monkeypatch):
    monkeypatch.setattr(env, "log_requests", True)
    with TestClient(app) as c:
        c.get("/api/meta", headers={"host": "192.168.1.199"})
        c.get("/nope", headers={"host": "192.168.1.199", "cf-ray": "x", "cf-ipcountry": "GB"})
        netstats.record_fetch(source="rightmove", host="www.rightmove.co.uk", status=429, ms=50, bytes_in=10)
        d = c.get("/api/network?hours=24", headers=admin_headers()).json()
        assert d["inbound"]["requests"] >= 2 and d["inbound"]["statuses"].get("400") == 1
        assert d["inbound"]["via"] == {"direct": 1, "cloudflare": 1} or d["inbound"]["via"].get("cloudflare") == 1
        assert d["outbound"]["hosts"][0]["rate_limited"] == 1
        for kind in ("ai", "scrapes", "emails", "app"):
            assert c.get(f"/api/logs/{kind}", headers=admin_headers()).status_code == 200
        assert c.get("/api/sites/house", headers=admin_headers()).json()["usage"]["requests_7d"] >= 1


@pytest.mark.parametrize("status,loc,state", [
    (302, "https://team.cloudflareaccess.com/login", "protected"), (403, "", "app-locked"),
    (200, "", "open"), (530, "", "no-route")])
def test_check_live_reads_a_real_page(monkeypatch, status, loc, state):
    import httpx
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return httpx.Response(status, headers={"location": loc} if loc else {})

    monkeypatch.setattr(cloudflare.httpx, "get", fake_get)
    res = cloudflare.check_live("zach.tamairnv.co.uk")
    assert res["state"] == state and res["ok"] == (state == "protected") and seen == ["https://zach.tamairnv.co.uk/"]
