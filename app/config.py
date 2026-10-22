"""Environment-level settings (secrets, paths). User-editable search settings live in profile.py."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="HF_", extra="ignore")

    data_dir: Path = Path("data")
    database_url: str | None = None  # default: sqlite in data_dir
    config_file: Path = Path("config.yaml")  # optional seed for the profile
    base_url: str = "http://localhost:8000"  # used for links in emails
    timezone: str = "Europe/London"

    # Optional HTTP basic auth for the web UI (recommended if exposed beyond LAN)
    ui_username: str | None = None
    ui_password: str | None = None

    # SMTP (e.g. Gmail with an app password: smtp.gmail.com:587, starttls)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False

    # Optional routing APIs for real commute times (fallback: distance-based estimate)
    ors_api_key: str | None = None  # openrouteservice.org, free tier
    google_maps_api_key: str | None = None  # Routes API, for public transport times

    # AI overview / Q&A on listings.
    #   auto: Claude API if a key is set, else the Claude Code CLI if it's installed (uses your Claude
    #   subscription), else off (AI buttons hidden).
    ai_provider: str = "auto"  # auto | api | claude_cli | off
    anthropic_api_key: str | None = None  # falls back to ANTHROPIC_API_KEY
    ai_model: str = "claude-opus-5-5"  # API only
    ai_effort: str = "medium"  # API only: low | medium | high
    claude_cmd: str = "claude"  # Claude Code CLI binary
    claude_model: str | None = None  # CLI only, e.g. "opus" or "sonnet"; default = your Claude Code default
    claude_timeout: int = 240

    # Scraping
    scheduler_enabled: bool = True
    user_agent: str = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
    request_delay_seconds: float = 4.0  # polite delay between requests to the same site
    dump_html: bool = False  # save raw pages to data/dumps for parser debugging
    playwright_enabled: bool = False  # use a headless browser for JS-heavy sites
    cache_photos: bool = True

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'house_finder.db').as_posix()}"

    @property
    def ai_key(self) -> str | None:
        import os
        return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
