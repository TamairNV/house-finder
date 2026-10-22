"""Load/save the Profile (stored as JSON in the settings table)."""
from __future__ import annotations

import logging

import yaml
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Recipient, Setting
from .profile import Profile

log = logging.getLogger(__name__)
PROFILE_KEY = "profile"


def load_profile(session: Session) -> Profile:
    row = session.get(Setting, PROFILE_KEY)
    if row is None:
        return Profile()
    return Profile.model_validate(row.value)


def save_profile(session: Session, profile: Profile) -> None:
    data = profile.model_dump(mode="json")
    row = session.get(Setting, PROFILE_KEY)
    if row is None:
        session.add(Setting(key=PROFILE_KEY, value=data))
    else:
        row.value = data


def seed_from_config(session: Session) -> None:
    """On first start, import config.yaml (if present) into the DB."""
    if session.get(Setting, PROFILE_KEY) is not None:
        return
    path = get_settings().config_file
    profile = Profile()
    recipients: list[dict] = []
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
        recipients = raw.pop("recipients", []) or []
        profile = Profile.model_validate(raw)
        log.info("Seeded profile from %s", path)
    save_profile(session, profile)
    for r in recipients:
        session.add(Recipient(**r))
