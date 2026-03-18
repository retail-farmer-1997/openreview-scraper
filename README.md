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

## Batch Download Workflow

After fetching metadata into the local DB, queue and drain the missing PDFs locally with:

```bash
./openreview-scraper fetch ICLR 2025 oral
./openreview-scraper worker run-downloads --enqueue-missing --workers 8
./openreview-scraper worker download-status
```

`worker run-downloads --enqueue-missing` reconciles the local DB, creates download jobs for papers
that still need PDF work, runs local workers in parallel, and shows a live interactive dashboard on
TTY terminals with per-worker paper progress plus an overall completion/utilization bar. Use
`--status-interval-seconds 0` to disable periodic plain-text status snapshots or `--json-output`
for machine-readable automation output. By default this worker flow only reconciles paper PDFs and
does not fetch the full forum; pass `--cache-forum` if you also want reviews/discussion cached while
draining the queue. OpenReview requests are throttled conservatively by default so queue drains
respect low server-side rate limits without requiring per-run flags, and the live/plain-text worker
status includes request counts plus any active throttle timer. When OpenReview returns 429s, the
scraper now uses an additive-increase / multiplicative-decrease controller on top of the reset
window wait so local workers automatically slow down after rate-limit pressure and only recover
gradually after clean requests.

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
- Optional throttle tuning:
  `OPENREVIEW_SCRAPER_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS`,
  `OPENREVIEW_SCRAPER_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS`

Compatibility fallbacks remain enabled during the port:

- Legacy repo variables: `RESEARCH_DB_PATH`, `RESEARCH_PAPERS_DIR`,
  `RESEARCH_OPENREVIEW_USERNAME`, `RESEARCH_OPENREVIEW_PASSWORD`,
  `RESEARCH_OPENREVIEW_TOKEN`
- OpenReview library variables: `OPENREVIEW_USERNAME`, `OPENREVIEW_PASSWORD`,
  `OPENREVIEW_TOKEN`

## Local Bootstrap

The repo-root launcher uses `uv` to keep installs reproducible and runnable on any box with
`pyproject.toml`:

```bash
uv sync
./openreview-scraper --help
./openreview-scraper --version
```

If you want a fully direct path, invoke via `uv run`:

```bash
uv run openreview-scraper --help
uv run openreview-scraper --version
```

You can also run the package directly for source-only development:

```bash
PYTHONPATH=src python3 -m openreview_scraper --help
PYTHONPATH=src python3 -m openreview_scraper fetch ICLR 2025 oral --json-output
```

You can install globally with Python package tools too:

```bash
python3 -m pip install -e .
openreview-scraper --help
```

## Validation

```bash
uv run python scripts/check_agent_docs.py
uv run python scripts/check_architecture.py
uv run python scripts/run_repo_checks.py tests
```

## Repo Guide

- [AGENTS.md](AGENTS.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [Agent Ops Index](docs/agent-ops/index.md)
- [Tech Debt Tracker](docs/exec-plans/tech-debt-tracker.md)
