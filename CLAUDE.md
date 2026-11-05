# OpenWolf

This project uses OpenWolf for context management. The always-on rules live in `.claude/rules/openwolf.md`; the hooks handle bookkeeping (anatomy index, memory log, read tracking) automatically.

For the full operating protocol (session handoff, memory discipline, bug logging), load the `openwolf` skill, or read `.wolf/OPENWOLF.md`. Regenerate the session handoff with `/handoff`.


# Notes for Claude

Server-wide notes for all projects: ~/.claude/CLAUDE.md (keep it updated).

Read `PLAN.md` first: it has the requirements, verified site structures, design decisions, deployment facts for
Tamair's server (TNV-server) and the prioritised next steps. Update it when anything changes.

- Run tests with `.venv/bin/pytest -q`. Parser tests live in `tests/test_scrapers.py`; add real-page fixtures under `tests/fixtures/`.
- Never commit `.env`, `config.yaml` or `data/`.
- Keep scraping polite (see `app/scrapers/base.py` Fetcher). Don't add captcha/login bypasses.
- Tamair prefers short, casual messages and lots of autonomous progress.
