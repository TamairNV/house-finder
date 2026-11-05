import pytest

from app import access_token, db
from app.config import get_settings
from tests import access_jwt


@pytest.fixture(autouse=True)
def access_login(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cf_team_domain", access_jwt.TEAM)
    monkeypatch.setattr(s, "cf_access_aud", access_jwt.AUD)
    monkeypatch.setattr(s, "access_managed_by_panel", False)  # the real .env turns this on
    monkeypatch.setattr(s, "log_requests", False)  # test_sites turns it on where it matters
    monkeypatch.setattr(s, "admin_emails", s.admin_emails)
    monkeypatch.setattr(s, "admin_access_aud", None)
    monkeypatch.setattr(access_token, "_signing_key", lambda token: access_jwt.KEY.public_key())


@pytest.fixture()
def session(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "scheduler_enabled", False)
    monkeypatch.setattr(s, "config_file", tmp_path / "missing.yaml")
    db.reset_engine(f"sqlite:///{tmp_path}/t.db")
    db.init_db()
    with db.session_scope() as sess:
        yield sess
    db.reset_engine()
    s.database_url = None
