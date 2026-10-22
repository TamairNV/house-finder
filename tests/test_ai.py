import json
import subprocess
from types import SimpleNamespace

import anthropic
import httpx2
from fastapi.testclient import TestClient

from app import ai
from app.config import get_settings
from app.main import app
from app.pipeline import enrich_and_score, upsert
from tests.test_pipeline import profile, raw


def _listing(session):
    from app.store import save_profile
    p = profile()
    save_profile(session, p)
    l, _ = upsert(session, raw(description="£110 PPPW bills included. Two bathrooms, big garden."), p)
    enrich_and_score(session, l, p)
    session.commit()
    return l, p


def test_context_has_group_and_listing_facts(session):
    l, p = _listing(session)
    ctx = ai.listing_context(l, p)
    assert f"People: {p.group_size}" in ctx and "big garden" in ctx and "All-in: £110 pppw" in ctx


def test_ai_disabled_without_key(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_provider", "api")
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    l, _ = _listing(session)
    with TestClient(app) as c:
        assert c.get("/api/meta").json()["ai_enabled"] is False
        r = c.post(f"/api/listings/{l.id}/ai/overview")
        assert r.status_code == 400 and "HF_ANTHROPIC_API_KEY" in r.json()["detail"]


def test_overview_and_questions_are_saved(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_provider", "api")
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    calls = []

    def fake_call(system, messages, max_tokens=4000):
        calls.append(messages)
        return f"## Verdict\nreply {len(calls)}"

    monkeypatch.setattr(ai, "_call", fake_call)
    l, _ = _listing(session)
    with TestClient(app) as c:
        r = c.post(f"/api/listings/{l.id}/ai/overview").json()
        assert r["ai_overview"] == "## Verdict\nreply 1"
        assert c.post(f"/api/listings/{l.id}/ai/ask", json={"question": "Is there a garden?"}).json()["a"] == "## Verdict\nreply 2"
        c.post(f"/api/listings/{l.id}/ai/ask", json={"question": "Parking?"})
        d = c.get(f"/api/listings/{l.id}").json()
        assert d["ai_overview"].endswith("reply 1") and [t["q"] for t in d["ai_chat"]] == ["Is there a garden?", "Parking?"]
        # follow-ups replay the overview and earlier Q&A as alternating turns, ending on the new question
        last = calls[-1]
        assert [m["role"] for m in last] == ["user", "assistant", "user", "assistant", "user"]
        assert "reply 1" in last[0]["content"][-1]["text"] and last[-1]["content"].startswith("Parking?")
        assert c.delete(f"/api/listings/{l.id}/ai/chat").status_code == 200
        assert c.get(f"/api/listings/{l.id}").json()["ai_chat"] == []
        assert c.post(f"/api/listings/{l.id}/ai/ask", json={"question": "  "}).status_code == 502


def test_photos_dropped_when_api_rejects_them(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_provider", "api")
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    l, p = _listing(session)
    l.photos = ["https://media.example.com/a.jpg", "http://insecure.example.com/b.jpg"]
    seen = []

    def fake_call(system, messages, max_tokens=4000):
        has_img = any(b.get("type") == "image" for b in messages[0]["content"])
        seen.append(has_img)
        if has_img:
            req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.BadRequestError("could not fetch image", response=httpx2.Response(400, request=req), body=None)
        return "ok"

    monkeypatch.setattr(ai, "_call", fake_call)
    assert ai.generate_overview(l, p) == "ok" and seen == [True, False]
    assert len(ai._photo_blocks(l)) == 1  # http:// photos are skipped


def test_refusal_becomes_aierror(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    resp = SimpleNamespace(stop_reason="refusal", content=[])
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp)))
    monkeypatch.setattr(ai, "_client", lambda: fake)
    try:
        ai._call("s", [{"role": "user", "content": "x"}])
    except ai.AIError as e:
        assert "declined" in str(e)
    else:
        raise AssertionError("expected AIError")


def test_updated_endpoint(session):
    with TestClient(app) as c:
        d = c.get("/api/updated").json()
        assert d["last_scrape"] is None and d["new_24h"] == 0


def test_provider_auto(monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "ai_provider", "auto")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(st, "anthropic_api_key", "sk-x")
    assert ai.provider() == "api"
    monkeypatch.setattr(st, "anthropic_api_key", None)
    monkeypatch.setattr(st, "claude_cmd", "/nonexistent/claude")
    assert ai.provider() is None
    monkeypatch.setattr(st, "claude_cmd", "sh")  # anything on PATH
    assert ai.provider() == "claude_cli"
    monkeypatch.setattr(st, "ai_provider", "off")
    assert ai.provider() is None


def test_cli_backend(session, monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "ai_provider", "claude_cli")
    monkeypatch.setattr(st, "claude_cmd", "sh")
    monkeypatch.setattr(ai.photos, "fetch", lambda u: (b"\xff\xd8jpeg", "image/jpeg"))
    l, p = _listing(session)
    l.photos = ["https://media.example.com/a.jpg"]
    l.ai_overview = "earlier overview"
    l.ai_chat = [{"q": "Garden?", "a": "A yard.", "at": "x"}]
    seen = {}

    def fake_run(cmd, input, **kw):
        seen["cmd"], seen["msg"], seen["cwd"] = cmd, json.loads(input), kw["cwd"]
        out = [{"type": "system", "subtype": "init"},
               {"type": "result", "subtype": "success", "is_error": False, "result": "Yes, two bathrooms.",
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 50},
                "total_cost_usd": 0.0123, "duration_ms": 4321, "modelUsage": {"claude-opus-5-5": {}}}]
        return subprocess.CompletedProcess(cmd, 0, "\n".join(map(json.dumps, out)), "")

    monkeypatch.setattr(ai.subprocess, "run", fake_run)
    turn = ai.ask(l, p, "How many bathrooms?")
    assert turn["a"] == "Yes, two bathrooms."
    cmd = seen["cmd"]
    assert cmd[:2] == ["sh", "-p"] and cmd[cmd.index("--tools") + 1] == "" and "Read" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == ai.SYSTEM
    blocks = seen["msg"]["message"]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["type"] == "base64"
    text = blocks[-1]["text"]
    assert text.startswith("(1 photo of") and "earlier overview" in text and "A yard." in text
    assert text.rstrip().endswith(ai.ASK_SUFFIX.strip()) and "Latest question (answer this)\nHow many bathrooms?" in text
    assert ai.last_usage() == {"provider": "claude_cli", "model": "claude-opus-5-5", "input_tokens": 1010,
                               "output_tokens": 50, "cost_usd": 0.0123, "duration_ms": 4321}


def test_cli_errors(monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "claude_cmd", "sh")
    res = {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}
    monkeypatch.setattr(ai.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(cmd, 1, json.dumps(res), ""))
    for stdout, err in [(json.dumps(res), "error_max_turns"), ("", "logged in")]:
        monkeypatch.setattr(ai.subprocess, "run", lambda cmd, input, _o=stdout, **kw: subprocess.CompletedProcess(cmd, 1, _o, "Please run /login"))
        try:
            ai._call_cli("s", [{"role": "user", "content": "x"}])
        except ai.AIError as e:
            assert err in str(e)
        else:
            raise AssertionError("expected AIError")



def test_ai_usage_is_logged_and_admin_only(session, monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "ai_provider", "api")
    monkeypatch.setattr(st, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(st, "admin_emails", "boss@example.com")
    l, _ = _listing(session)
    session.commit()
    calls = iter(["Short.\n<!-- more -->\nLong.", ai.AIError("nope")])

    def fake_call(system, messages, max_tokens=4000):
        ai._set_usage(provider="api", model="m", input_tokens=100, output_tokens=20)
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ai, "_call", fake_call)
    boss = {"Cf-Access-Authenticated-User-Email": "Boss@example.com"}
    pal = {"Cf-Access-Authenticated-User-Email": "pal@example.com"}
    with TestClient(app) as c:
        assert c.post(f"/api/listings/{l.id}/ai/overview", headers=pal).status_code == 200
        assert c.post(f"/api/listings/{l.id}/ai/ask", json={"question": "Garden?"}, headers=pal).status_code == 502
        assert c.get("/api/ai/usage", headers=pal).status_code == 403
        d = c.get("/api/ai/usage", headers=boss).json()
    assert [(r["kind"], r["user"], r["ok"]) for r in d["rows"]] == [("ask", "pal@example.com", False), ("overview", "pal@example.com", True)]
    assert d["rows"][0]["question"] == "Garden?" and d["rows"][0]["error"] == "nope"
    assert d["people"] == [{"user": "pal@example.com", "requests": 2, "failed": 1, "input_tokens": 200, "output_tokens": 40,
                            "cost_usd": 0.0, "last": d["rows"][0]["at"]}]
