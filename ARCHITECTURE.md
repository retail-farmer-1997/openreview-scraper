# Architecture

## System Purpose
CLI-first OpenReview paper ingestion, local storage, and analysis toolkit without the dashboard/UI surface.

## Current Domain Boundaries
- `src/openreview_scraper/cli.py`: public CLI contract and presentation-only argument handling.
- `src/openreview_scraper/service.py`: business operations shared by synchronous CLI commands and worker flows.
- `src/openreview_scraper/openreview.py`: OpenReview HTTP client, retry policy, and PDF/download integration boundary.
- `src/openreview_scraper/db.py`: SQLite schema, migrations, and repository-style persistence helpers.
- `src/openreview_scraper/models.py`: typed domain objects and parsing helpers with no I/O concerns.
- `src/openreview_scraper/settings.py`: runtime configuration and environment-driven path resolution.
- `src/openreview_scraper/storage.py`: storage runtime selection, cache-path/locator mapping, and future GCS sync lifecycle hooks.
- `src/openreview_scraper/worker.py`: background queue orchestration built on `service`, not a second business-logic layer.
- `scripts/bootstrap_local_cli.py`: repo-local launcher bootstrap for `./openreview-scraper`.

## Dependency Direction
- `openreview_scraper.__main__` may only call `openreview_scraper.cli`.
- `cli` may depend on `service`, `db`, `models`, `settings`, `storage`, `openreview`, `worker`, and `observability` when that module exists.
- `service` may depend on `db`, `models`, `settings`, `storage`, and `openreview`.
- `worker` may depend on `service`, `db`, `settings`, and `storage`.
- `db`, `models`, `settings`, and `storage` are leaf-oriented modules and must not depend on CLI or worker code.
- `openreview` may depend on `models` and `settings`, but must not depend on persistence or CLI layers.

## Enforced Dependency Rules
- `python3 scripts/check_agent_docs.py` validates the agent-first docs contract.
- `python3 scripts/check_architecture.py` validates import boundaries for the core Python package.
- Update this section whenever a repeated architecture review comment becomes a script or CI check.

## Invariants
- The CLI-only repo must not depend on or embed `research_ui`/dashboard code from the source repo.
- Command implementations must be idempotent where re-runs are expected (`fetch`, `download`, queue drains, migrations).
- External API access must use bounded timeouts and explicit retry behavior for transient failures only.
- Persistent writes must be recoverable and avoid partial-state corruption; file writes should become atomic before download features land.
- Environment configuration must live in `settings.py`; command modules should not hardcode user paths or credentials.

## Near-Term Target
Port the source repo's backend CLI surface into a clean `openreview_scraper` package with no UI baggage, no legacy `research` package name, and a smaller but stricter validation harness.
