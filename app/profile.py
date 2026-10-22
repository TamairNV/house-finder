"""The search profile: everything the user can tweak from the Settings page.

Stored as one JSON document in the ``settings`` table so the schema can evolve
without migrations. ``config.example.yaml`` mirrors these fields.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

TravelMode = Literal["walk", "cycle", "transit", "drive"]


class Place(BaseModel):
    """A place someone goes to a lot (uni building, work, gym...)."""
    label: str
    postcode: str | None = None
    lat: float | None = None
    lng: float | None = None
    mode: TravelMode = "walk"
    max_minutes: int = 25  # beyond this the listing is penalised (or excluded if hard)
    hard_limit: bool = False
    weight: float = 1.0  # relative importance vs this person's other places
    days_per_week: int = 5  # informational, also scales weight


class Person(BaseModel):
    name: str
    email: str | None = None
    places: list[Place] = Field(default_factory=list)


class SourceLocation(BaseModel):
    """How each site names the search area."""
    label: str = "Newcastle upon Tyne"
    rightmove: str | None = "REGION^984"
    zoopla: str | None = "newcastle-upon-tyne"
    openrent: str | None = "newcastle-upon-tyne"
    spareroom: str | None = "newcastle_upon_tyne"
    sturents: str | None = "newcastle"
    afs: str | None = "newcastle"
    unihomes: str | None = "newcastle"


class SourceConfig(BaseModel):
    enabled: bool = True
    max_pages: int = 5
    fetch_details: bool = True  # visit each new listing's page for full info


class BillsConfig(BaseModel):
    """Assumptions used when a listing doesn't include bills. Monthly, whole house."""
    # Energy: Ofgem cap forecast for 2026/27 (pocketwise.co.uk, Mar 2026): elec ~25p/kWh +
    # ~60p/day, gas ~6.5p/kWh + ~32p/day. Consumption scaled from Fused Bills' Newcastle
    # student house figures (5-bed: 17,000 kWh gas, 4,100 kWh elec ≈ £202/month).
    elec_unit_p: float = 25.0
    elec_standing_p_day: float = 60.0
    gas_unit_p: float = 6.5
    gas_standing_p_day: float = 32.0
    elec_kwh_base: float = 900.0
    elec_kwh_per_person: float = 640.0
    gas_kwh_base: float = 4000.0
    gas_kwh_per_person: float = 2600.0
    epc_multipliers: dict[str, float] = Field(default_factory=lambda: {
        "A": 0.65, "B": 0.75, "C": 0.88, "D": 1.0, "E": 1.15, "F": 1.3, "G": 1.45})
    water_monthly: float = 42.0  # Northumbrian Water average (Fused Bills)
    water_per_person_monthly: float = 3.0
    broadband_monthly: float = 32.0
    tv_licence: bool = False
    tv_licence_annual: float = 174.50  # check tvlicensing.co.uk each April
    council_tax_monthly: float = 0.0  # full-time student households are exempt
    contingency_pct: float = 10.0  # extra cushion on top of the estimate


class EmailRules(BaseModel):
    great_score_threshold: float = 80.0
    reminder_every_days: int = 3  # how often to re-mention great places not yet acted on
    max_reminders: int = 5


class Weights(BaseModel):
    price: float = 30
    commute: float = 30
    bathrooms: float = 10
    move_in: float = 15
    bills_included: float = 5
    photos: float = 5
    preferred_area: float = 5


class Profile(BaseModel):
    name: str = "House hunt 2027/28"
    locations: list[SourceLocation] = Field(default_factory=lambda: [SourceLocation()])
    people: list[Person] = Field(default_factory=lambda: [
        Person(name="Tamair", places=[
            Place(label="Newcastle University", postcode="NE1 7RU", lat=54.9800, lng=-1.6150,
                  mode="walk", max_minutes=25)]),
    ])
    group_size: int = 4  # hard limit: listing must fit exactly this many people
    extra_bedrooms_allowed: int = 0  # 1 = also accept houses with one spare room
    min_bathrooms_per_person: float = 0.34  # 0.34 ≈ 1 bathroom per 3 people
    unknown_bathrooms_ok: bool = True

    # Budget, per person per week, *including* bills (estimated if not included)
    budget_pppw: float = 150.0
    budget_buffer_pppw: float = 15.0  # listings up to budget+buffer are kept but penalised

    # Move-in window
    target_move_in: date = date(2027, 7, 1)
    earliest_move_in: date = date(2027, 6, 1)
    latest_move_in: date = date(2027, 9, 15)
    unknown_move_in_ok: bool = True  # keep listings with no date (flagged)

    furnished: Literal["any", "furnished", "unfurnished"] = "any"
    require_student_friendly: bool = True  # drop 'professionals only' listings
    exclude_keywords: list[str] = Field(default_factory=lambda: ["room only", "single room", "studio", "en-suite room in", "female only", "male only"])
    preferred_areas: list[str] = Field(default_factory=lambda: [
        "Jesmond", "Heaton", "Sandyford", "Spital Tongues", "Shieldfield", "Ouseburn", "Gosforth", "Fenham"])
    excluded_areas: list[str] = Field(default_factory=list)
    max_distance_km: float = 8.0  # from the first person's first place; drops far-flung NE results

    sources: dict[str, SourceConfig] = Field(default_factory=lambda: {
        "rightmove": SourceConfig(max_pages=10),
        "zoopla": SourceConfig(enabled=False),  # Cloudflare 403 for plain HTTP; needs Playwright
        "openrent": SourceConfig(),
        "spareroom": SourceConfig(max_pages=3),
        "sturents": SourceConfig(max_pages=3),  # pages = sub-sitemaps walked
        "afs": SourceConfig(max_pages=8),
        "unihomes": SourceConfig(max_pages=10, fetch_details=False),  # search JSON has nearly everything
    })
    scrape_interval_hours: float = 4.0
    scrape_quiet_hours: tuple[int, int] = (1, 7)  # don't scrape between these hours

    bills: BillsConfig = Field(default_factory=BillsConfig)
    weights: Weights = Field(default_factory=Weights)
    email: EmailRules = Field(default_factory=EmailRules)

    @model_validator(mode="after")
    def _check(self) -> "Profile":
        if self.group_size < 1:
            raise ValueError("group_size must be >= 1")
        if self.earliest_move_in > self.latest_move_in:
            raise ValueError("earliest_move_in must be before latest_move_in")
        return self

    @property
    def budget_max_pppw(self) -> float:
        return self.budget_pppw + self.budget_buffer_pppw

    @property
    def allowed_bedrooms(self) -> range:
        return range(self.group_size, self.group_size + self.extra_bedrooms_allowed + 1)

    def all_places(self) -> list[tuple[Person, Place]]:
        return [(p, pl) for p in self.people for pl in p.places]
