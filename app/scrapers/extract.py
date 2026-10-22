"""Generic extraction helpers shared by all site scrapers.

Sites change markup often, so scrapers try, in order:
1. embedded JSON state (__NEXT_DATA__, window.PAGE_MODEL, JSON-LD...)
2. listing cards located by their link pattern, parsed as text
3. label/value pairs on detail pages ("Available From", "Bills included?")
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..textparse import clean


def soupify(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text_of(el: Tag | BeautifulSoup | None) -> str:
    if el is None:
        return ""
    return clean(el.get_text(" ", strip=True))


# ------------------------------------------------------------ embedded JSON

def next_data(soup: BeautifulSoup) -> dict | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            return None
    return None


def json_ld(soup: BeautifulSoup) -> list[Any]:
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _balanced(s: str, start: int) -> str | None:
    """Return the JSON object/array starting at s[start] using bracket matching."""
    open_ch = s[start]
    close_ch = {"{": "}", "[": "]"}.get(open_ch)
    if not close_ch:
        return None
    depth, i, in_str, esc = 0, start, False, False
    while i < len(s):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
        i += 1
    return None


def js_assignment(html: str, name: str) -> Any | None:
    """Parse ``window.NAME = {...}`` / ``var NAME = [...]`` / ``NAME: {...}``."""
    m = re.search(rf"(?:window\.|var\s+|let\s+|const\s+)?{re.escape(name)}\s*=\s*([\[{{])", html)
    if not m:
        return None
    blob = _balanced(html, m.start(1))
    if not blob:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # JS arrays of numbers/strings with single quotes or trailing commas
        fixed = re.sub(r",\s*([\]}])", r"\1", blob.replace("'", '"'))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None


def js_arrays(html: str) -> dict[str, list]:
    """All ``var UPPERCASE = [...]`` arrays (OpenRent style)."""
    out: dict[str, list] = {}
    for m in re.finditer(r"(?:var|let|const)\s+([A-Z][A-Z0-9_]+)\s*=\s*\[", html):
        val = js_assignment(html[m.start():], m.group(1))
        if isinstance(val, list):
            out[m.group(1)] = val
    return out


def walk(obj: Any) -> Iterator[dict]:
    """Yield every dict nested anywhere inside ``obj``."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def find_dicts(obj: Any, *required: str, any_of: tuple[str, ...] = ()) -> list[dict]:
    """Dicts that have all ``required`` keys and at least one of ``any_of``."""
    found, seen = [], set()
    for d in walk(obj):
        if all(k in d for k in required) and (not any_of or any(k in d for k in any_of)):
            if id(d) not in seen:
                seen.add(id(d))
                found.append(d)
    return found


def dget(d: Any, path: str, default: Any = None) -> Any:
    """``dget(d, "location.latitude")``; integer parts index lists."""
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
        if cur is None:
            return default
    return cur


def first(d: Any, *paths: str) -> Any:
    for p in paths:
        v = dget(d, p)
        if v not in (None, "", [], {}):
            return v
    return None


IMG_RE = re.compile(r"https?://[^\s\"'<>]+?\.(?:jpe?g|png|webp)(?:\?[^\s\"'<>]*)?", re.I)


def image_urls(obj: Any, host_hint: str | None = None) -> list[str]:
    """Collect image URLs from any JSON/str structure, preserving order, deduped."""
    blob = obj if isinstance(obj, str) else json.dumps(obj)
    blob = blob.replace("\\/", "/").replace("\\u002F", "/")
    urls = []
    for u in IMG_RE.findall(blob):
        if host_hint and host_hint not in u:
            continue
        if u not in urls:
            urls.append(u)
    return urls


def to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("£", "").strip())
    except ValueError:
        return None


def to_int(v: Any) -> int | None:
    f = to_float(v)
    return int(f) if f is not None else None


# ------------------------------------------------------------ cards

def cards_by_link(soup: BeautifulSoup, href_re: str, base_url: str, max_climb: int = 8) -> list[tuple[str, str, Tag]]:
    """Find listing cards via their link pattern.

    Returns (listing_id, absolute_url, card_element). The card is the largest
    ancestor of the link that still contains only one distinct listing id.
    """
    pat = re.compile(href_re)
    by_id: dict[str, tuple[str, Tag]] = {}
    for a in soup.find_all("a", href=True):
        m = pat.search(a["href"])
        if not m:
            continue
        lid = m.group(1)
        if lid not in by_id:
            by_id[lid] = (urljoin(base_url, a["href"].split("#")[0]), a)
    out = []
    for lid, (url, a) in by_id.items():
        card: Tag = a
        node: Tag | None = a
        for _ in range(max_climb):
            node = node.parent if node is not None else None
            if node is None or node.name in ("body", "html", "main"):
                break
            ids = {mm.group(1) for x in node.find_all("a", href=True) if (mm := pat.search(x["href"]))}
            if len(ids) > 1:
                break
            card = node
        out.append((lid, url, card))
    return out


def card_images(card: Tag, base_url: str) -> list[str]:
    urls: list[str] = []
    for img in card.find_all(["img", "source"]):
        for attr in ("src", "data-src", "data-lazy", "data-original", "srcset", "data-srcset"):
            v = img.get(attr)
            if not v:
                continue
            u = v.split(",")[0].strip().split(" ")[0]
            if u.startswith("data:"):
                continue
            u = urljoin(base_url, u)
            if u not in urls and re.search(r"\.(jpe?g|png|webp)", u, re.I):
                urls.append(u)
    return urls


# ------------------------------------------------------------ label/value pairs

def label_values(soup: BeautifulSoup) -> dict[str, str]:
    """Collect label->value pairs from tables, definition lists and 'Label: value' lines.

    Labels are lower-cased and stripped of punctuation for easy lookup.
    """
    pairs: dict[str, str] = {}

    def put(k: str, v: str) -> None:
        k = re.sub(r"[^a-z0-9 ]", "", clean(k).lower()).strip()
        v = clean(v)
        if k and len(k) < 40 and k not in pairs:
            pairs[k] = v

    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) >= 2:
            put(text_of(cells[0]), _cell_value(cells[1]))
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                put(text_of(dt), _cell_value(dd))
    text = soup.get_text("\n", strip=True)
    for line in text.split("\n"):
        m = re.match(r"^([A-Za-z][A-Za-z /?#&'-]{2,35}):\s*(.+)$", line.strip())
        if m:
            put(m.group(1), m.group(2))
    # "Label" on one line, value on the next (common in React layouts)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i, l in enumerate(lines[:-1]):
        if l.endswith(":") and len(l) < 40:
            put(l[:-1], lines[i + 1])
    return pairs


def _cell_value(cell: Tag) -> str:
    t = text_of(cell)
    if t:
        return t
    # icon-only cells: tick / cross
    classes = " ".join(" ".join(x.get("class", [])) for x in cell.find_all(True)) + " " + " ".join(cell.get("class", []))
    classes = classes.lower()
    if re.search(r"check|tick|yes|true|success", classes):
        return "Yes"
    if re.search(r"times|cross|close|no\b|false|danger", classes):
        return "No"
    return ""


def yes_no(v: str | None) -> bool | None:
    if not v:
        return None
    v = v.strip().lower()
    if v.startswith(("yes", "y ", "true", "included", "✓", "✔")) or v == "y":
        return True
    if v.startswith(("no", "false", "not", "✗", "✘", "excluded")):
        return False
    return None


def meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return tag.get("content") if tag else None


LATLNG_RES = [
    re.compile(r'"lat(?:itude)?"\s*:\s*"?(-?\d{1,2}\.\d{3,})"?\s*,\s*"(?:lng|lon|long|longitude)"\s*:\s*"?(-?\d{1,3}\.\d{3,})'),
    re.compile(r"lat(?:itude)?\s*[=:]\s*['\"]?(-?\d{1,2}\.\d{3,}).{0,80}?(?:lng|lon|long|longitude)\s*[=:]\s*['\"]?(-?\d{1,3}\.\d{3,})", re.S),
    re.compile(r"data-lat(?:itude)?=['\"](-?\d{1,2}\.\d{3,})['\"][^>]*data-(?:lng|lon|long|longitude)=['\"](-?\d{1,3}\.\d{3,})"),
    re.compile(r"[?&](?:center|q|ll|markers)=(-?\d{1,2}\.\d{3,})(?:,|%2C)(-?\d{1,3}\.\d{3,})"),
]


def find_latlng(html: str, uk_only: bool = True) -> tuple[float, float] | None:
    for rx in LATLNG_RES:
        for m in rx.finditer(html):
            lat, lng = float(m.group(1)), float(m.group(2))
            if not uk_only or (49.8 < lat < 60.9 and -8.7 < lng < 1.8):
                return lat, lng
    return None
