"""Free-text parsing helpers.

Most UK listing sites only expose the interesting facts (per-person rent, bills
packages, availability dates, bathrooms) in free text, so this module is the
heart of normalisation. Every function is pure and covered by tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

WEEKS_PER_MONTH = 52 / 12  # 4.333...

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


def clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_money(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------- prices

@dataclass
class PriceMention:
    amount: float
    period: str  # "pw" | "pcm" | "pa"
    per_person: bool
    incl_bills: bool | None  # True if the mention itself says "inc bills"
    is_bills_cost: bool  # the amount is a bills package / bills cost, not rent
    is_deposit: bool
    raw: str

    @property
    def weekly(self) -> float:
        if self.period == "pw":
            return self.amount
        if self.period == "pcm":
            return self.amount / WEEKS_PER_MONTH
        return self.amount / 52


_UNIT = (
    r"p\.?\s?p\.?\s?p\.?\s?w\.?|p\.?\s?p\.?\s?p\.?\s?c?m\.?|pp\s?pw|pp\s?pcm|pp\s?pm|"
    r"per\s+person\s+per\s+week|per\s+person\s+per\s+month|per\s+person\s*/\s*week|"
    r"per\s+person\s*/\s*month|per\s+room\s+per\s+week|per\s+room\s+per\s+month|"
    r"per\s+calendar\s+month|"
    r"pw|p/w|per\s+week|a\s+week|/\s?week|/\s?wk|weekly|"
    r"pcm|p/m|per\s+month|a\s+month|/\s?month|monthly|"
    r"pa|p/a|per\s+annum|per\s+year|/\s?year"
)
_PRICE_RE = re.compile(
    r"£\s?(?P<amt>\d[\d,]*(?:\.\d{1,2})?)(?:\s*(?P<k>k)\b)?\s*(?P<unit>" + _UNIT + r")?(?![a-z])",
    re.IGNORECASE,
)
BILLS_INC_RE = re.compile(
    r"(inc(?:l(?:uding|usive|udes|\.)?)?\.?\s*(?:of\s+)?(?:all\s+)?(?:utility\s+|the\s+)?bills|"
    r"bills\s*(?:are\s+)?(?:inc(?:l(?:uded|usive|\.)?)?\.?|included)|with\s+bills|all[- ]inclusive|"
    r"bills\s*:\s*included|bills\s+included\??\s*:?\s*yes)",
    re.IGNORECASE,
)
BILLS_EXC_RE = re.compile(
    r"(exc(?:l(?:uding|usive|udes|\.)?)?\.?\s*(?:of\s+)?bills|bills\s+(?:are\s+)?(?:not\s+included|exc(?:l(?:uded)?)?\.?|extra|payable)|"
    r"plus\s+bills|\+\s*bills|bills\s+included\??\s*:?\s*no)",
    re.IGNORECASE,
)
_BILLS_COST_CTX = re.compile(r"bills?\s+package|utilit(?:y|ies)\s+package|bills?\s+(?:cost|charge|fee)|for\s+bills|bills\s+(?:at|of|are|is)\s*$|^\s*bills", re.IGNORECASE)
_DEPOSIT_CTX = re.compile(r"deposit|holding|admin|fee|reservation|referenc", re.IGNORECASE)
_EQUIV_ROOM = re.compile(r"(equivalent\s+to|per\s+room)", re.IGNORECASE)


def classify_unit(unit: str | None) -> tuple[str | None, bool]:
    """Return (period, per_person) for a matched unit string."""
    if not unit:
        return None, False
    u = re.sub(r"[\s.]", "", unit.lower())
    if u.startswith(("perpersonperweek", "perperson/week", "perroomperweek")) or (u.startswith("pp") and u.endswith("w")):
        return "pw", True
    if u.startswith(("perpersonpermonth", "perperson/month", "perroompermonth")) or (
            u.startswith("pp") and u.endswith(("cm", "pm"))):
        return "pcm", True
    if u in ("pw", "p/w", "perweek", "aweek", "/week", "/wk", "weekly"):
        return "pw", False
    if u in ("pcm", "p/m", "permonth", "amonth", "/month", "monthly", "percalendarmonth"):
        return "pcm", False
    if u in ("pa", "p/a", "perannum", "peryear", "/year"):
        return "pa", False
    return None, False


def extract_prices(text: str) -> list[PriceMention]:
    """Find every £ amount with a recognisable period in ``text``."""
    text = clean(text)
    out: list[PriceMention] = []
    for m in _PRICE_RE.finditer(text):
        period, per_person = classify_unit(m.group("unit"))
        if period is None:
            continue
        amount = parse_money(m.group("amt"))
        if amount is None or amount <= 0:
            continue
        if m.group("k"):
            amount *= 1000
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 45]
        nxt = after.find("£")
        after_short = after[: nxt if nxt >= 0 else 25][:25]
        incl: bool | None = None
        if BILLS_INC_RE.search(after_short) or re.search(r"\(\s*$", before) and BILLS_INC_RE.search(after_short):
            incl = True
        elif BILLS_EXC_RE.search(after_short):
            incl = False
        # "£139pppw (£166pppw inc bills)": the inc-bills phrase is *after* the 2nd
        # price; for the first price it is beyond the next "£" so not in after_short.
        after_says_bills = bool(re.match(r"\s*(?:for\s+)?(?:bills|utilit)", after, re.I)
                                or re.match(r"\s*\w*\s*bills?\s+package", after, re.I))
        is_bills_cost = bool(_BILLS_COST_CTX.search(before[-25:]) or (after_says_bills and incl is not True))
        is_deposit = bool(_DEPOSIT_CTX.search(before[-20:]))
        # SpareRoom "(Equivalent to £366 per room)" — a per-person figure even
        # though the unit is missing; handled by caller if needed.
        if _EQUIV_ROOM.search(before[-20:]) and period == "pcm":
            per_person = True
        out.append(PriceMention(amount, period, per_person, incl, is_bills_cost, is_deposit, m.group(0).strip()))
    # SpareRoom "(Equivalent to £366 per room)" has no period word; treat as pcm per person.
    for m in re.finditer(r"equivalent\s+to\s+£\s?(\d[\d,]*(?:\.\d+)?)\s+per\s+room", text, re.I):
        amt = parse_money(m.group(1))
        if amt:
            out.append(PriceMention(amt, "pcm", True, None, False, False, m.group(0)))
    return out


@dataclass
class RentGuess:
    pppw: float | None  # base rent per person per week
    total_pcm: float | None  # whole-property rent per month
    bills_included: bool | None
    bills_pppw: float | None  # explicit bills package cost, if stated
    source: str  # which rule produced it, for debugging


def guess_rent(text: str, bedrooms: int | None, people: int | None = None,
               structured_amount: float | None = None, structured_period: str | None = None,
               structured_per_person: bool = False) -> RentGuess:
    """Combine structured price fields and free text into a per-person weekly rent.

    Free-text per-person mentions win over whole-property structured prices,
    because agents typically put "£115 PPPW" in the description while the
    portal field holds the whole-house pcm figure.
    """
    mentions = [p for p in extract_prices(text) if not p.is_deposit]
    bills_inc = detect_bills_included(text)
    bills_pkg = None
    for p in mentions:
        if p.is_bills_cost and p.per_person or (p.is_bills_cost and p.period == "pw" and p.amount < 60):
            bills_pkg = round(p.weekly, 2)
            break
    divisor = people or bedrooms

    pp = [p for p in mentions if p.per_person and not p.is_bills_cost and 30 <= p.weekly <= 600]
    if pp:
        base = [p for p in pp if p.incl_bills is not True]
        inc = [p for p in pp if p.incl_bills is True]
        if base:
            chosen = min(base, key=lambda p: p.weekly)  # "from £X pppw" -> lowest
            pppw = round(chosen.weekly, 2)
            if inc and bills_pkg is None:
                diff = round(min(inc, key=lambda p: p.weekly).weekly - pppw, 2)
                if 0 < diff < 80:
                    bills_pkg = diff
            if bills_inc is True and inc and not bills_pkg:
                bills_inc = None  # "£139 pppw (£166 inc bills)" means base excludes bills
            if bills_pkg:
                bills_inc = False if chosen.incl_bills is not True else True
            total = round(pppw * WEEKS_PER_MONTH * divisor, 2) if divisor else None
            return RentGuess(pppw, total, bills_inc, bills_pkg, "text-pppw")
        chosen = min(inc, key=lambda p: p.weekly)
        pppw = round(chosen.weekly, 2)
        total = round(pppw * WEEKS_PER_MONTH * divisor, 2) if divisor else None
        return RentGuess(pppw, total, True, bills_pkg, "text-pppw-inc")

    if structured_amount:
        period = structured_period or "pcm"
        weekly = structured_amount / WEEKS_PER_MONTH if period == "pcm" else (
            structured_amount if period == "pw" else structured_amount / 52)
        if structured_per_person:
            pppw = round(weekly, 2)
            total = round(pppw * WEEKS_PER_MONTH * divisor, 2) if divisor else None
        else:
            total = round(weekly * WEEKS_PER_MONTH, 2)
            pppw = round(weekly / divisor, 2) if divisor else None
        return RentGuess(pppw, total, bills_inc, bills_pkg, "structured")

    whole = [p for p in mentions if not p.per_person and not p.is_bills_cost and p.weekly > 50]
    if whole:
        chosen = max(whole, key=lambda p: p.weekly)
        total = round(chosen.weekly * WEEKS_PER_MONTH, 2)
        pppw = round(chosen.weekly / divisor, 2) if divisor else None
        return RentGuess(pppw, total, bills_inc if chosen.incl_bills is None else chosen.incl_bills,
                         bills_pkg, "text-whole")
    return RentGuess(None, None, bills_inc, bills_pkg, "none")


# ---------------------------------------------------------------- bills

def detect_bills_included(text: str) -> bool | None:
    """True / False when the text clearly says so, else None."""
    t = clean(text)
    if not t:
        return None
    if re.search(r"bills\s+included\??\s*:?\s*no\b", t, re.I):
        return False
    if re.search(r"bills\s+included\??\s*:?\s*yes\b", t, re.I):
        return True
    if re.search(r"(compulsory|mandatory|additional|optional)\s+(?:additional\s+)?(?:£\s?\d+\S*\s+)?bills?\s+package", t, re.I):
        return False
    exc = BILLS_EXC_RE.search(t)
    inc = BILLS_INC_RE.search(t)
    if inc and not exc:
        return True
    if exc and not inc:
        return False
    if inc and exc:
        return inc.start() < exc.start()  # first statement usually describes the headline price
    return None


# ---------------------------------------------------------------- dates

def _year_for(month: int, day: int, today: date) -> int:
    """Pick the next occurrence of month/day (listings rarely refer to the past)."""
    y = today.year
    try:
        if date(y, month, day) < today.replace(day=1):
            y += 1
    except ValueError:
        pass
    return y


def parse_date_fragment(s: str, today: date) -> date | None:
    s = clean(s).lower().replace(",", " ")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    # 07/07/2027 or 7-7-27
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    # 2027-07-01
    m = re.search(r"\b(20\d\d)-(\d{2})-(\d{2})\b", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # 1 Aug 2027 / 26 august 2027 / 1 aug
    m = re.search(rf"\b(\d{{1,2}})\s+(?:of\s+)?({_MONTH_RE})\.?\s*(\d{{4}})?\b", s)
    if m:
        d, mo = int(m.group(1)), MONTHS[m.group(2)]
        y = int(m.group(3)) if m.group(3) else _year_for(mo, d, today)
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    # August 1st 2027 / July 2027 / August
    m = re.search(rf"\b({_MONTH_RE})\.?\s*(\d{{1,2}})?\s*(\d{{4}})?\b", s)
    if m:
        mo = MONTHS[m.group(1)]
        d = int(m.group(2)) if m.group(2) and int(m.group(2)) <= 31 else 1
        y = int(m.group(3)) if m.group(3) else _year_for(mo, d, today)
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


_AVAIL_RE = re.compile(
    r"(?:let\s+)?(?:available|availability|avail\.?|move[- ]in|tenancy\s+start(?:s|ing)?|start\s+date|from)"
    r"\s*(?:date)?\s*(?:from|on)?\s*[:\-]?\s*(?P<rest>.{0,30})",
    re.IGNORECASE,
)


def parse_available(text: str, today: date | None = None) -> tuple[date | None, bool]:
    """Return (available_from, is_now).

    ``is_now`` is True for "available now/immediately/today".
    """
    today = today or date.today()
    t = clean(text)
    for m in _AVAIL_RE.finditer(t):
        rest = m.group("rest")
        if re.match(r"\s*(now|immediately|today|asap|straight\s*away)", rest, re.I):
            return today, True
        d = parse_date_fragment(rest, today)
        if d:
            return d, d <= today
    return None, False


def academic_year_hint(text: str) -> tuple[int, int] | None:
    """Detect '2027/28', '2027-2028', '27/28' style academic-year mentions."""
    m = re.search(r"\b(20\d\d)\s*[/\-–]\s*(20)?(\d\d)\b", text)
    if m:
        y1 = int(m.group(1))
        y2 = int((m.group(2) or "20") + m.group(3))
        if y2 == y1 + 1:
            return y1, y2
    m = re.search(r"\b(\d\d)\s*/\s*(\d\d)\b\s*(?:academic|year|tenanc|let)", text, re.I)
    if m and int(m.group(2)) == int(m.group(1)) + 1:
        return 2000 + int(m.group(1)), 2000 + int(m.group(2))
    return None


# ---------------------------------------------------------------- rooms

_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "single": 1, "double": 2}


def _num(tok: str) -> int | None:
    tok = tok.lower()
    if tok.isdigit():
        return int(tok)
    return _WORDNUM.get(tok)


def parse_bedrooms(text: str) -> int | None:
    t = clean(text)
    m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]?(?:(?:double|single|large|good[- ]sized)\s+)?(?:bed(?:room)?s?|br)\b", t, re.I)
    if m:
        return _num(m.group(1))
    m = re.search(r"bedrooms?\s*:?\s*(\d{1,2})\b", t, re.I)
    return int(m.group(1)) if m else None


def parse_bathrooms(text: str) -> float | None:
    t = clean(text)
    counts: list[int] = []
    for m in re.finditer(r"\b(\d{1,2}|one|two|three|four|five|six)\s*(?:x\s*)?(?:shower\s*rooms?|bath(?:room)?s?|wcs?)\b", t, re.I):
        n = _num(m.group(1))
        if n is not None:
            counts.append(n)
    m = re.search(r"bathrooms?\s*:?\s*(\d{1,2})\b", t, re.I)
    if m:
        counts.append(int(m.group(1)))
    ens = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|all)\s*(?:x\s*)?en[- ]?suites?\b", t, re.I)
    total = max(counts) if counts else None
    if ens:
        ens_n = _num(ens.group(1)) if ens.group(1).lower() != "all" else None
        if ens_n and (total is None or total < ens_n):
            total = ens_n
    return float(total) if total is not None else None


def parse_furnished(text: str) -> str | None:
    t = clean(text).lower()
    if "part furnished" in t or "part-furnished" in t or "partly furnished" in t:
        return "part"
    if "unfurnished" in t:
        return "unfurnished"
    if "furnished" in t:
        return "furnished"
    return None


POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)
OUTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\b")


def parse_postcode(text: str) -> tuple[str | None, str | None]:
    """Return (full_postcode, outcode)."""
    t = clean(text)
    m = POSTCODE_RE.search(t)
    if m:
        return f"{m.group(1).upper()} {m.group(2).upper()}", m.group(1).upper()
    for m in OUTCODE_RE.finditer(t):
        oc = m.group(1)
        if re.match(r"^(NE|SR|DH|TS|LS|M|B|L|S|G|EH|BS|CF|NG|LE|CB|OX|SW|SE|N|E|W|NW|EC|WC|[A-Z]{2})\d", oc) and oc.isupper():
            return None, oc
    return None, None


def is_student_let(text: str) -> bool | None:
    t = clean(text).lower()
    if re.search(r"no\s+students|students\s+not\s+(?:accepted|allowed)|professionals?\s+only", t):
        return False
    if re.search(r"student|pppw|academic\s+year|hmo", t):
        return True
    return None


def let_agreed(text: str) -> bool:
    return bool(re.search(r"\blet\s+agreed\b|\bunder\s+offer\b|\blet\b\s*$|\bnow\s+let\b", clean(text), re.I))
