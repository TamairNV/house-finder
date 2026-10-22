import pytest

from app import db
from app.config import get_settings


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
