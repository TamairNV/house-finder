# Notes for Claude

Read `PLAN.md` first: it has the requirements, verified site structures, design decisions, deployment facts for
Tamair's server (TNV-server) and the prioritised next steps. Update it when anything changes.

- Run tests with `.venv/bin/pytest -q`. Parser tests live in `tests/test_scrapers.py`; add real-page fixtures under `tests/fixtures/`.
- Never commit `.env`, `config.yaml` or `data/`.
- Keep scraping polite (see `app/scrapers/base.py` Fetcher). Don't add captcha/login bypasses.
- Tamair prefers short, casual messages and lots of autonomous progress.
