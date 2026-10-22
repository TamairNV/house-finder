"""Estimate household bills when a listing doesn't include them."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .profile import BillsConfig
from .textparse import WEEKS_PER_MONTH


@dataclass
class BillsEstimate:
    electricity: float
    gas: float
    water: float
    broadband: float
    tv_licence: float
    council_tax: float
    contingency: float
    total_monthly: float
    per_person_monthly: float
    per_person_weekly: float

    def to_dict(self) -> dict:
        return {k: round(v, 2) for k, v in asdict(self).items()}


def estimate_bills(people: int, cfg: BillsConfig, epc: str | None = None, bedrooms: int | None = None) -> BillsEstimate:
    """Monthly whole-house bills for ``people`` occupants.

    Consumption is linear in occupants (calibrated so 5 people ≈ 4,100 kWh
    electricity + 17,000 kWh gas a year, the Fused Bills Newcastle 5-bed figure),
    then scaled by EPC rating. Bigger houses than needed cost a bit more to heat.
    """
    people = max(people, 1)
    mult = cfg.epc_multipliers.get((epc or "D").upper()[:1], 1.0)
    size_factor = 1.0 + 0.08 * max((bedrooms or people) - people, 0)
    elec_kwh = (cfg.elec_kwh_base + cfg.elec_kwh_per_person * people) * mult
    gas_kwh = (cfg.gas_kwh_base + cfg.gas_kwh_per_person * people) * mult * size_factor
    elec = (elec_kwh * cfg.elec_unit_p + 365 * cfg.elec_standing_p_day) / 100 / 12
    gas = (gas_kwh * cfg.gas_unit_p + 365 * cfg.gas_standing_p_day) / 100 / 12
    water = cfg.water_monthly + cfg.water_per_person_monthly * people
    tv = cfg.tv_licence_annual / 12 if cfg.tv_licence else 0.0
    subtotal = elec + gas + water + cfg.broadband_monthly + tv + cfg.council_tax_monthly
    contingency = subtotal * cfg.contingency_pct / 100
    total = subtotal + contingency
    pp_month = total / people
    return BillsEstimate(elec, gas, water, cfg.broadband_monthly, tv, cfg.council_tax_monthly,
                         contingency, total, pp_month, pp_month / WEEKS_PER_MONTH)
