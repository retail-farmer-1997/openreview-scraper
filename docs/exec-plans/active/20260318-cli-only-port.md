# CLI-Only Port Foundation

## Objective
Stand up `openreview-scraper` as a clean repo for only the backend CLI functionality now housed in
`~/code/repos/research/`, with a renamed internal package, strict boundaries, and no dashboard/UI
surface.

## Scope
- Keep only the backend CLI command surface from the source repo.
- Use `openreview_scraper` as the internal Python package name.
- Preserve the source repo's functional command contract:
  `fetch`, `abstract`, `download`, `list`, `tag`, `note`, `show`, `overview`, `reviews`,
  `discussion`, `db *`, and `worker *`.
- Recreate the repo-local launcher/bootstrap flow so `./openreview-scraper` remains the main entrypoint.
- Build the port in small slices with tests and boundary checks attached to each slice.

## Non-Goals
- Porting `research_ui`, the dashboard server, or any localhost UI route.
- Preserving the legacy internal package name `research`.
- Preserving every implementation detail from the source repo when a smaller, clearer design is better.

## Risks
- The source CLI is intertwined with DB, worker, and forum-cache behavior, so a naive copy risks dragging in UI assumptions or legacy naming.
- Runtime configuration needs an explicit compatibility decision: preserve legacy env vars temporarily or switch immediately to `OPENREVIEW_SCRAPER_*`.
- Worker and download flows touch concurrency, retries, and local-file integrity; these are not safe to port without dedicated regression coverage.
- Source tests assume a larger repo context; some will need to be rewritten rather than copied.

## Validation
- `uv run python scripts/run_repo_checks.py all`

## Dependency-Ordered Task List

### Parallelizable Now
1. Runtime configuration, package rename compatibility, and pure model parsing
   - Port `settings.py` and `models.py` into `openreview_scraper`.
   - Introduce `OPENREVIEW_SCRAPER_*` env vars while preserving legacy `RESEARCH_*` fallbacks.
   - Keep the current OpenReview library env fallbacks (`OPENREVIEW_USERNAME`, `OPENREVIEW_PASSWORD`, `OPENREVIEW_TOKEN`).
   - Port and adapt unit tests for settings/model parsing.
2. Persistence and migrations
   - Port SQLite migrations and `db.py` helpers with the renamed package imports.
   - Preserve idempotent migration behavior, normalized author/keyword relations, forum cache tables, and queue helpers.
   - Port migration/DB integration tests and adapt them to the renamed package plus new env alias strategy.
3. OpenReview client and network hardening
   - Port `openreview.py` with retry, auth-hint, PDF validation, and atomic write behavior.
   - Drop dashboard/observability coupling from the CLI-only repo; keep the network and PDF integrity behavior.
   - Port and adapt network tests, using module stubs for `openreview` in unit tests.

### Depends On Foundations Above
4. Service-layer orchestration
   - Port `service.py` once `models`, `settings`, `db`, and `openreview` are in place.
   - Preserve idempotent fetch/download behavior and forum-cache hydration.
5. Worker flows
   - Port `worker.py` after `service.py` and `db.py` land.
   - Preserve queue claiming, lease handling, and reconcile/download workflows.
6. CLI presentation layer
   - Replace the current stubs with the full CLI-only command surface after the underlying modules exist.
   - Keep the repo CLI dashboard-free and preserve the command contract from the source repo.
   - Preserve the source repo's Click-based command behavior so the contract and adapted tests stay aligned.

### Final Integration And Hardening
7. Integration tests, launcher checks, and live smoke runs
   - Port/adapt CLI and service/worker integration tests for the CLI-only repo.
   - Run the local launcher and selected authenticated OpenReview flows using the already-configured credentials.
8. Harness cleanup
   - Update docs, execution-plan status, and any guardrail/test entrypoints that drift during the port.
   - Keep the CLI-only repo aligned with the automated-development scaffold rather than letting port work bypass it.

## Status Log
- 2026-03-18: Audited the empty target repo and confirmed it had no local files to preserve.
- 2026-03-18: Audited `~/code/repos/research/` and extracted the CLI-only command surface, bootstrap path, validation entrypoints, and architecture boundaries.
- 2026-03-18: Installed and customized the automated-development harness for this repo.
- 2026-03-18: Added a minimal Python/package/bootstrap skeleton so future feature slices have stable file locations and validation hooks.
- 2026-03-18: Adjusted the local launcher bootstrap to run the module from `src/` inside `.venv` so clean-clone smoke tests do not depend on network access for build tooling.
- 2026-03-18: Chose a compatibility-first runtime config strategy: add `OPENREVIEW_SCRAPER_*` names for the clean repo while continuing to accept legacy `RESEARCH_*` env vars so existing credentials and scripts keep working during the port.
- 2026-03-18: Split the port into three immediately parallelizable foundations (`settings/models`, `db+migrations`, `openreview client`) followed by integration-heavy `service`, `worker`, `cli`, and full validation slices.
- 2026-03-18: Ported the runtime configuration and data-model foundations into `openreview_scraper`, preserving `RESEARCH_*` compatibility while introducing the repo-local `OPENREVIEW_SCRAPER_*` names.
- 2026-03-18: Ported SQLite migrations and DB helpers, including queue, forum-cache, and relation-table behavior, and tightened connection lifecycle handling so test runs do not leak sqlite resources.
- 2026-03-18: Ported the OpenReview client layer with retry/auth-hint/PDF-integrity behavior and removed dashboard/observability coupling from the CLI-only repo.
- 2026-03-18: Ported the service and worker layers, preserving idempotent fetch/download flows plus queue claim, lease, and reconcile behavior.
- 2026-03-18: Chose Click for the target CLI because the source command contract and adapted test suite are Click-native; ported the full dashboard-free command surface instead of keeping the earlier argparse stub.
- 2026-03-18: Added and adapted unit and integration coverage for settings, models, migrations, DB integration, service/worker flows, and the CLI contract.
- 2026-03-18: Validation passed with `python3 scripts/check_agent_docs.py`, `python3 scripts/check_architecture.py`, and `python3 scripts/run_repo_checks.py tests`.
- 2026-03-18: Live smoke-tested the authenticated CLI path against OpenReview with repo-local credentials and confirmed `fetch ICLR 2025 oral --json-output` created 213 papers in a fresh temp database, followed by successful `db stats --json-output`.
- 2026-03-18: Updated local launcher to use `uv` for bootstrap (`uv venv` + `uv sync`) and documented `uv sync` / `uv run` as the primary local install/run path.
