from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import enrich_and_score, upsert
from tests.test_pipeline import profile, raw


def test_api_roundtrip(session):
    from app.store import save_profile
    p = profile()
    save_profile(session, p)
    l, _ = upsert(session, raw(description="£110 PPPW bills included"), p)
    enrich_and_score(session, l, p)
    session.commit()
    with TestClient(app) as c:
        res = c.get("/api/listings").json()
        assert res["count"] == 1 and res["items"][0]["total_pppw"] == 110
        assert c.get("/").status_code == 200 and c.get(f"/listing/{l.id}").status_code == 200
        assert c.patch(f"/api/listings/{l.id}", json={"favourite": True, "user_status": "shortlisted"}).status_code == 200
        assert c.get("/api/listings", params={"favourite": True}).json()["count"] == 1
        assert c.get("/api/listings", params={"status": "rejected"}).json()["count"] == 0
        prof = c.get("/api/profile").json()["profile"]
        prof["group_size"] = 5
        r = c.put("/api/profile", json=prof)
        assert r.status_code == 200 and r.json()["rescored"] == 1
        assert c.get("/api/listings").json()["count"] == 0  # 4-bed no longer fits 5 people
        bad = dict(prof, earliest_move_in="2027-12-01")
        assert c.put("/api/profile", json=bad).status_code == 422
        rid = c.post("/api/recipients", json={"name": "T", "email": "t@example.com"}).json()["id"]
        assert c.get(f"/api/recipients/{rid}/preview").status_code == 200
        assert c.get("/api/status").json()["smtp_configured"] is False
        assert c.get("/img", params={"u": "https://evil.example.com/x.jpg"}).status_code == 400
