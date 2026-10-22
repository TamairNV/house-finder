"""AI overview and Q&A for a listing.

Two backends (see ``Settings.ai_provider``):
- ``api``: the Claude API with HF_ANTHROPIC_API_KEY (pay as you go).
- ``claude_cli``: the Claude Code CLI in headless mode (``claude -p``), which runs on the
  owner's Claude subscription. Tools are disabled and photos are sent inline, so the model
  only sees the listing.

The overview is stored on the listing so it's only generated once; questions and answers
are appended to ``Listing.ai_chat`` and replayed as conversation history for follow-ups.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time

import anthropic

from . import photos

from .bills import estimate_bills
from .config import get_settings
from .models import Listing, utcnow
from .profile import Profile

log = logging.getLogger(__name__)

MAX_PHOTOS = 6
MAX_CHAT_TURNS = 20  # older Q&A is dropped from the prompt (still stored)

SYSTEM = """You help a group of university students choose a house share for next academic year.
You're given one listing (scraped from a UK property site), some photos, the group's requirements,
and figures the app worked out (all-in price per person per week, bill estimates, commute estimates).

Be direct and practical, like a friend who has rented a lot of student houses in Newcastle. Use British English.
Stick to what the listing data and photos show. When something matters but isn't stated, say it's unknown
and suggest asking the agent, rather than guessing. Commute times marked as estimates come from
straight-line distance, so treat them as rough."""

# Answers are shown short-first: everything after this marker sits behind a "Show more" button.
MORE = "<!-- more -->"

OVERVIEW_PROMPT = f"""Write an overview of this listing for the group, in two parts.

Part 1, the short version (what everyone reads): one line with the verdict and a rating like **7/10**, then
exactly 3 short bullets: the best thing, the biggest worry, and the all-in cost vs budget. Under 60 words total.

Then a line containing only {MORE}

Part 2, the detail, in Markdown with these sections:

## Verdict
One or two sentences: is it worth a viewing, and why. End with a rating like **7/10**.

## Good
3-6 bullets.

## Watch out for
3-6 bullets. Red flags, missing info, anything that looks off (e.g. price vs similar places,
small rooms, tiny or no living room, bathroom count, move-in date, "professionals only", bills caps).

## From the photos
What the photos suggest about condition, furnishing, kitchen, bathrooms, light and space. Note anything
the photos avoid showing (e.g. no living room or bathroom pictures).

## Money
All-in cost per person per week and per month, what's included, deposit and holding fee if known,
and how it compares with the group's budget.

## Ask the agent
4-6 specific questions to ask before or at a viewing.

Keep part 2 under about 350 words."""

ASK_SUFFIX = f"""

(Answer in 1-3 short sentences first. If there's genuinely useful extra detail, add a line containing only
{MORE} and put the detail after it. Markdown is fine.)"""


class AIError(Exception):
    pass


_usage = threading.local()


def _set_usage(**kw) -> None:
    _usage.value = {k: v for k, v in kw.items() if v is not None}


def last_usage() -> dict:
    """Token/cost figures from the most recent call on this thread (for the AI log)."""
    return getattr(_usage, "value", None) or {}


def provider() -> str | None:
    st = get_settings()
    p = st.ai_provider
    if p == "auto":
        return "api" if st.ai_key else ("claude_cli" if shutil.which(st.claude_cmd) else None)
    if p == "api":
        return "api" if st.ai_key else None
    if p == "claude_cli":
        return "claude_cli" if shutil.which(st.claude_cmd) else None
    return None


def enabled() -> bool:
    return provider() is not None


def _client() -> anthropic.Anthropic:
    key = get_settings().ai_key
    if not key:
        raise AIError("AI isn't set up: add HF_ANTHROPIC_API_KEY to .env and restart.")
    return anthropic.Anthropic(api_key=key, timeout=120.0)


def listing_context(l: Listing, profile: Profile) -> str:
    """Plain-text dump of everything we know, for the prompt."""
    people = []
    for p in profile.people:
        places = ", ".join(f"{pl.label} ({pl.postcode or '?'}) by {pl.mode}, max {pl.max_minutes} min" for pl in p.places)
        people.append(f"- {p.name}: {places or 'no places set'}")
    bills = estimate_bills(profile.group_size, profile.bills, l.epc, l.bedrooms)

    def v(x, unit=""):
        return "unknown" if x in (None, "", []) else f"{x}{unit}"

    commutes = "\n".join(
        f"- {c['person']} to {c['place']}: {round(c['minutes'])} min by {c['mode']} ({c['km']} km"
        f"{', estimate' if c.get('estimated') else ''}; their max {c.get('max_minutes', '?')} min)"
        for c in (l.commutes or [])) or "- unknown (no location)"
    features = "\n".join(f"- {k}" for k in (l.key_features or [])) or "- none listed"
    description = (l.description or (l.raw or {}).get("card_text") or "No description scraped.").strip()[:6000]

    return f"""# The group
- People: {profile.group_size} (house must have exactly {profile.group_size} bedrooms{f" (+{profile.extra_bedrooms_allowed} spare ok)" if profile.extra_bedrooms_allowed else ""})
- Budget: £{profile.budget_pppw:g} per person per week INCLUDING bills (up to £{profile.budget_max_pppw:g} at a push)
- Move in: ideally {profile.target_move_in:%d %b %Y}, acceptable {profile.earliest_move_in:%d %b %Y} to {profile.latest_move_in:%d %b %Y}
- Furnished: {profile.furnished}; at least {profile.min_bathrooms_per_person:g} bathrooms per person
- Preferred areas: {', '.join(profile.preferred_areas) or 'none'}
- Where people need to get to:
{chr(10).join(people)}

# The listing ({l.source}, {l.url})
- Title: {v(l.title)}
- Address: {v(l.address)} {l.postcode or l.outcode or ''} (area: {v(l.area)})
- Type: {v(l.property_type)}; bedrooms {v(l.bedrooms)}; bathrooms {v(l.bathrooms)}; furnished {v(l.furnished)}; EPC {v(l.epc)}
- Rent: £{v(l.rent_pppw and round(l.rent_pppw))} pppw{f' (£{l.rent_total_pcm:,.0f} pcm whole house)' if l.rent_total_pcm else ''}
- Bills: {'included' if l.bills_included else f'not included; agent states £{l.bills_pppw_stated:.0f} pppw' if l.bills_pppw_stated else f'not included; our estimate £{l.bills_pppw_estimate or 0:.0f} pppw (whole house ~£{bills.total_monthly:.0f}/month)'}
- All-in: £{v(l.total_pppw and round(l.total_pppw))} pppw
- Deposit: {v(l.deposit and f'£{l.deposit:,.0f}')}; minimum term: {v(l.min_term_months, ' months')}
- Available: {'now' if l.available_now else v(l.available_from)}
- Agent: {v(l.agent_name)}
- Status on site: {l.site_status}; first seen by us {l.first_seen:%d %b %Y}

## Key features
{features}

## Description
{description}

# What the app worked out
- Score: {round(l.score or 0)}/100; passes the group's hard rules: {'yes' if l.passes_filters else 'no'}
- Fails: {'; '.join(l.filter_reasons or []) or 'nothing'}
- Warnings: {'; '.join(l.warnings or []) or 'none'}
- Commutes:
{commutes}
"""


def _photo_blocks(l: Listing) -> list[dict]:
    return [{"type": "image", "source": {"type": "url", "url": u}}
            for u in (l.photos or [])[:MAX_PHOTOS] if u.startswith("https://")]


def _call(system: str, messages: list[dict], max_tokens: int = 4000) -> str:
    st = get_settings()
    try:
        resp = _client().beta.messages.create(
            model=st.ai_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": st.ai_effort},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise AIError("The Anthropic API key was rejected; check HF_ANTHROPIC_API_KEY.") from e
    except anthropic.RateLimitError as e:
        raise AIError("Rate limited by the Anthropic API; try again in a minute.") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Couldn't reach the Anthropic API.") from e
    if (u := getattr(resp, "usage", None)) is not None:
        _set_usage(provider="api", model=getattr(resp, "model", None) or st.ai_model, output_tokens=u.output_tokens,
                   input_tokens=u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0))
    if resp.stop_reason == "refusal":
        raise AIError("The model declined to answer this one.")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise AIError("Empty response from the model.")
    return text


def _with_photos(l: Listing, text_blocks: list[dict]) -> list[dict]:
    return [*_photo_blocks(l), *text_blocks]


def _first_turn(l: Listing, profile: Profile, prompt: str, photos: bool) -> list[dict]:
    blocks = [{"type": "text", "text": listing_context(l, profile)}, {"type": "text", "text": prompt}]
    return [{"role": "user", "content": _with_photos(l, blocks) if photos else blocks}]


def _call_with_photo_fallback(build) -> str:
    """Listing sites sometimes refuse image fetches; retry text-only if the photos break the request."""
    _usage.value = None
    if provider() == "claude_cli":
        return _call_cli(SYSTEM, build(True))
    try:
        return _call(SYSTEM, build(True))
    except anthropic.BadRequestError as e:
        log.warning("AI request with photos failed (%s); retrying without photos", e)
        try:
            return _call(SYSTEM, build(False))
        except anthropic.BadRequestError as e2:
            raise AIError(f"The API rejected the request: {e2.message}") from e2


def log_usage(s, *, user: str | None, listing_id: int | None, kind: str, question: str | None = None,
              error: str | None = None, started: float | None = None) -> None:
    """Record one AI request in the ai_usage table (caller commits)."""
    from .models import AIUsage
    u = last_usage()
    if "duration_ms" not in u and started is not None:
        u["duration_ms"] = int((time.monotonic() - started) * 1000)
    s.add(AIUsage(user_email=user, listing_id=listing_id, kind=kind, question=(question or "")[:300] or None,
                  ok=error is None, error=(error or "")[:300] or None, provider=u.get("provider") or provider(),
                  model=u.get("model"), input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                  cost_usd=u.get("cost_usd"), duration_ms=u.get("duration_ms")))


def generate_overview(l: Listing, profile: Profile) -> str:
    text = _call_with_photo_fallback(lambda photos: _first_turn(l, profile, OVERVIEW_PROMPT, photos))
    l.ai_overview = text
    l.ai_overview_at = utcnow()
    return text


def ask(l: Listing, profile: Profile, question: str) -> dict:
    question = question.strip()[:2000]
    if not question:
        raise AIError("Ask a question first.")

    def build(photos: bool) -> list[dict]:
        intro = "Here's a listing we're looking at." + (
            f"\n\nYou already wrote this overview of it:\n\n{l.ai_overview}" if l.ai_overview else "")
        msgs = _first_turn(l, profile, intro, photos)
        history = (l.ai_chat or [])[-MAX_CHAT_TURNS:]
        msgs.append({"role": "assistant", "content": "Got it. What would you like to know?"})
        for turn in history:
            msgs.append({"role": "user", "content": turn["q"]})
            msgs.append({"role": "assistant", "content": turn["a"]})
        msgs.append({"role": "user", "content": question + ASK_SUFFIX})
        return msgs

    answer = _call_with_photo_fallback(build)
    turn = {"q": question, "a": answer, "at": utcnow().isoformat()}
    l.ai_chat = [*(l.ai_chat or []), turn]  # reassign so SQLAlchemy sees the JSON change
    return turn


# ------------------------------------------------------------------ Claude Code CLI backend

# Listing text is untrusted, so the model gets no tools: `--tools ""` turns them all off and this list
# is a second guard in case a CLI version ignores it.
CLI_DISALLOWED = ["Bash", "Edit", "MultiEdit", "Write", "Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch",
                  "NotebookEdit", "NotebookRead", "Task", "TodoWrite", "Agent", "Skill"]
MAX_PHOTO_BYTES = 3_500_000
CLI_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def _inline_photo(block: dict) -> dict | None:
    """URL image block -> base64 image block (the CLI can't fetch URLs itself without tools)."""
    try:
        data, ctype = photos.fetch(block["source"]["url"])
    except photos.PhotoError:
        return None
    if ctype not in CLI_IMAGE_TYPES or len(data) > MAX_PHOTO_BYTES:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": ctype, "data": base64.b64encode(data).decode()}}


def _flatten(messages: list[dict]) -> list[dict]:
    """The CLI takes one user turn, so fold the conversation history into it."""
    images: list[dict] = []
    parts: list[str] = []
    for i, m in enumerate(messages):
        content = m["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        texts = []
        for b in blocks:
            if b.get("type") == "image":
                if (img := _inline_photo(b)) is not None:
                    images.append(img)
            elif b.get("type") == "text":
                texts.append(b["text"])
        text = "\n\n".join(texts)
        if i == 0:
            parts.append(text)
        elif m["role"] == "assistant":
            parts.append(f"## Your earlier reply\n{text}")
        else:
            parts.append(f"## {'Latest question (answer this)' if i == len(messages) - 1 else 'Earlier question'}\n{text}")
    if images:
        parts.insert(0, f"({len(images)} photo{'s' if len(images) > 1 else ''} of the property attached.)")
    return [*images, {"type": "text", "text": "\n\n".join(parts)}]


def _call_cli(system: str, messages: list[dict]) -> str:
    st = get_settings()
    cmd = [st.claude_cmd, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--system-prompt", system, "--tools", "", "--strict-mcp-config", "--disallowedTools", *CLI_DISALLOWED]
    if st.claude_model:
        cmd += ["--model", st.claude_model]
    line = json.dumps({"type": "user", "message": {"role": "user", "content": _flatten(messages)}})
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hf-ai-") as cwd:  # empty dir: no CLAUDE.md or project files
        try:
            proc = subprocess.run(cmd, input=line + "\n", capture_output=True, text=True, cwd=cwd, timeout=st.claude_timeout)
        except FileNotFoundError as e:
            raise AIError(f"Claude Code CLI not found ({st.claude_cmd}).") from e
        except subprocess.TimeoutExpired as e:
            raise AIError("Claude took too long to answer; try again.") from e
    result = None
    for out in proc.stdout.splitlines():
        try:
            ev = json.loads(out)
        except ValueError:
            continue
        if ev.get("type") == "result":
            result = ev
    if result is None:
        log.warning("claude CLI failed (rc=%s): %s", proc.returncode, proc.stderr[-2000:])
        hint = " Is Claude Code logged in on the server?" if "login" in (proc.stderr + proc.stdout).lower() else ""
        raise AIError(f"Claude Code didn't return an answer.{hint}")
    u = result.get("usage") or {}
    _set_usage(provider="claude_cli", model=next(iter(result.get("modelUsage") or {}), None) or st.claude_model,
               input_tokens=sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
               output_tokens=u.get("output_tokens") or 0, cost_usd=result.get("total_cost_usd"),
               duration_ms=result.get("duration_ms") or int((time.monotonic() - t0) * 1000))
    text = (result.get("result") or "").strip()
    if result.get("is_error") or result.get("subtype") != "success" or not text:
        raise AIError(f"Claude Code error: {text[:300] or result.get('subtype')}")
    return text
