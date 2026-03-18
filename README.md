# openreview-scraper

CLI-only reboot of the OpenReview tooling from `~/code/repos/research/`.

This repository intentionally excludes the dashboard/UI surface. The goal is a smaller, cleaner
backend-focused codebase with a renamed internal package, tighter boundaries, and a repo-local
agent harness that keeps implementation work inspectable.

## Current State

- Agent harness is installed and customized for the CLI-only target.
- The CLI/backend port is implemented for the OpenReview, database, service, and worker flows.
- The active implementation record lives in
  [`docs/exec-plans/active/20260318-cli-only-port.md`](docs/exec-plans/active/20260318-cli-only-port.md).
- The repo intentionally excludes the dashboard/UI surface.

## Command Surface

Top-level commands:

- `fetch`
- `abstract`
- `download`
- `list`
- `tag`
- `note`
- `show`
- `overview`
- `reviews`
- `discussion`

Grouped commands:

- `db migrate`
- `db status`
- `db stats`
- `worker enqueue-sync`
- `worker run-once`
- `worker enqueue-download`
- `worker enqueue-downloads`
- `worker run-downloads`
- `worker download-status`

No dashboard command belongs in this repo.

## Runtime Configuration

Primary repo-local environment variables:

- `OPENREVIEW_SCRAPER_DB_PATH`
- `OPENREVIEW_SCRAPER_PAPERS_DIR`
- `OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME`
- `OPENREVIEW_SCRAPER_OPENREVIEW_PASSWORD`

Normal users should authenticate with username/password. The token setting remains available only
for callers that already have an OpenReview session token and do not want the client to log in on
their behalf.

- Optional existing-session variable: `OPENREVIEW_SCRAPER_OPENREVIEW_TOKEN`

Compatibility fallbacks remain enabled during the port:

- Legacy repo variables: `RESEARCH_DB_PATH`, `RESEARCH_PAPERS_DIR`,
  `RESEARCH_OPENREVIEW_USERNAME`, `RESEARCH_OPENREVIEW_PASSWORD`,
  `RESEARCH_OPENREVIEW_TOKEN`
- OpenReview library variables: `OPENREVIEW_USERNAME`, `OPENREVIEW_PASSWORD`,
  `OPENREVIEW_TOKEN`

## Local Bootstrap

The repo-root launcher creates `.venv` and dispatches to `python -m openreview_scraper` from the
local `src/` tree:

```bash
./openreview-scraper --help
./openreview-scraper --version
```

You can also run the package directly during development:

```bash
PYTHONPATH=src python3 -m openreview_scraper --help
PYTHONPATH=src python3 -m openreview_scraper fetch ICLR 2025 oral --json-output
```

## Validation

```bash
python3 scripts/check_agent_docs.py
python3 scripts/check_architecture.py
python3 scripts/run_repo_checks.py tests
```

## Repo Guide

- [AGENTS.md](AGENTS.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [Agent Ops Index](docs/agent-ops/index.md)
- [Tech Debt Tracker](docs/exec-plans/tech-debt-tracker.md)
