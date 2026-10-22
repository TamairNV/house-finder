"""Hard filters + a 0-100 score for each listing against the profile."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .geo import Commute, haversine_km
from .models import Listing
from .profile import Profile


@dataclass
class Evaluation:
    passes: bool
    reasons: list[str] = field(default_factory=list)  # why it failed a hard filter
    warnings: list[str] = field(default_factory=list)  # soft concerns / unknowns
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def price_component(total: float, budget: float, budget_max: float) -> float:
    low = budget * 0.75
    if total <= low:
        return 1.0
    if total <= budget:
        return 0.6 + 0.4 * (budget - total) / (budget - low)
    if total <= budget_max and budget_max > budget:
        return 0.6 * (budget_max - total) / (budget_max - budget)
    return 0.0


def commute_component(c: Commute) -> float:
    m, mx = c.minutes, c.max_minutes
    if m <= mx * 0.5:
        return 1.0
    if m <= mx:
        return 1.0 - 0.7 * (m - mx * 0.5) / (mx * 0.5)
    return _clamp(0.3 * (1.5 * mx - m) / (0.5 * mx))


def move_in_component(avail: date, p: Profile) -> float:
    delta = abs((avail - p.target_move_in).days)
    if delta <= 14:
        return 1.0 - delta / 140  # 1.0 -> 0.9
    edge = max((p.target_move_in - p.earliest_move_in).days, (p.latest_move_in - p.target_move_in).days, 15)
    return _clamp(0.9 - 0.6 * (delta - 14) / max(edge - 14, 1), 0.3, 0.9)


def text_blob(l: Listing) -> str:
    return " ".join(filter(None, [l.title, l.address, l.description, " ".join(l.key_features or [])])).lower()


def evaluate(l: Listing, p: Profile, commutes: list[Commute], today: date | None = None,
             primary: tuple[float, float] | None = None) -> Evaluation:
    today = today or date.today()
    ev = Evaluation(passes=True)
    fail, warn = ev.reasons.append, ev.warnings.append
    people = p.group_size
    blob = text_blob(l)

    # --- hard filters -------------------------------------------------------
    if l.site_status != "active":
        fail(f"Listing is {l.site_status.replace('_', ' ')}")

    if l.bedrooms is None:
        fail("Bedroom count unknown")
    elif l.bedrooms not in p.allowed_bedrooms:
        allowed = f"{p.group_size}" if p.extra_bedrooms_allowed == 0 else f"{p.group_size}-{p.allowed_bedrooms[-1]}"
        fail(f"{l.bedrooms} bedrooms (need {allowed})")

    if l.bathrooms is None:
        (warn if p.unknown_bathrooms_ok else fail)("Bathroom count unknown")
    elif l.bathrooms / people + 1e-9 < p.min_bathrooms_per_person:
        fail(f"{l.bathrooms:g} bathroom(s) for {people} people")

    if l.total_pppw is None:
        fail("Price unknown")
    elif l.total_pppw > p.budget_max_pppw:
        fail(f"£{l.total_pppw:.0f} pppw inc bills > £{p.budget_max_pppw:.0f} max")
    elif l.total_pppw > p.budget_pppw:
        warn(f"Over budget (inside buffer): £{l.total_pppw:.0f} pppw")
    if l.bills_included is not True and l.bills_pppw_stated is None and l.total_pppw is not None:
        warn("Bills not included: estimated")

    if l.available_from is None:
        (warn if p.unknown_move_in_ok else fail)("Move-in date unknown")
    elif l.available_from < p.earliest_move_in:
        if l.available_now or l.available_from <= today:
            fail("Available now (needs move-in before your window)")
        else:
            fail(f"Available {l.available_from:%d %b %Y}, before {p.earliest_move_in:%d %b %Y}")
    elif l.available_from > p.latest_move_in:
        fail(f"Available {l.available_from:%d %b %Y}, after {p.latest_move_in:%d %b %Y}")

    if p.furnished != "any" and l.furnished and l.furnished != "part" and l.furnished != p.furnished:
        fail(f"{l.furnished.capitalize()} (want {p.furnished})")

    if l.student_let is False:
        (fail if p.require_student_friendly else warn)("Listing says no students / professionals only")

    for kw in p.exclude_keywords:
        if kw and kw.lower() in blob:
            fail(f"Contains excluded keyword '{kw}'")
            break
    for area in p.excluded_areas:
        if area and area.lower() in ((l.area or "") + " " + (l.address or "")).lower():
            fail(f"In excluded area {area}")
            break

    if l.lat is None:
        warn("No location: commute unknown")
    elif primary and p.max_distance_km:
        d = haversine_km(l.lat, l.lng, *primary)
        if d > p.max_distance_km:
            fail(f"{d:.1f} km from {p.people[0].places[0].label if p.people and p.people[0].places else 'centre'}")
    if l.geo_precision in ("outcode", "area"):
        warn("Approximate location (postcode district only)")

    for c in commutes:
        if c.hard_limit and c.minutes > c.max_minutes:
            fail(f"{c.person}: {c.minutes:.0f} min to {c.place} (limit {c.max_minutes})")

    ev.passes = not ev.reasons

    # --- score --------------------------------------------------------------
    w = p.weights
    comp: dict[str, float] = {}
    comp["price"] = price_component(l.total_pppw, p.budget_pppw, p.budget_max_pppw) if l.total_pppw else 0.0
    if commutes:
        vals = [(commute_component(c), c.weight) for c in commutes]
        avg = sum(v * wt for v, wt in vals) / max(sum(wt for _, wt in vals), 1e-9)
        comp["commute"] = 0.7 * avg + 0.3 * min(v for v, _ in vals)  # fairness: worst-off person counts
    else:
        comp["commute"] = 0.3
    if l.bathrooms is None:
        comp["bathrooms"] = 0.4
    else:
        ratio = l.bathrooms / people
        lo = p.min_bathrooms_per_person
        comp["bathrooms"] = 1.0 if ratio >= 0.5 else _clamp(0.5 + 0.5 * (ratio - lo) / max(0.5 - lo, 1e-9))
    comp["move_in"] = move_in_component(l.available_from, p) if l.available_from else 0.4
    comp["bills_included"] = 1.0 if l.bills_included else (0.6 if l.bills_pppw_stated else (0.5 if l.bills_included is None else 0.3))
    comp["photos"] = _clamp(len(l.photos or []) / 8)
    pref = [a.lower() for a in p.preferred_areas]
    loc_text = ((l.area or "") + " " + (l.address or "") + " " + (l.title or "")).lower()
    comp["preferred_area"] = 1.0 if any(a in loc_text for a in pref) else 0.0

    weights = {k: getattr(w, k) for k in comp}
    total_w = sum(weights.values()) or 1
    ev.score = round(100 * sum(comp[k] * weights[k] for k in comp) / total_w, 1)
    ev.breakdown = {k: round(v, 3) for k, v in comp.items()}
    return ev
