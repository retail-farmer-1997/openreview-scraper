# Architecture Invariants

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Allowed Internal Dependencies
- `openreview_scraper.__main__` may import only `openreview_scraper.cli`.
- `openreview_scraper.cli` may import `db`, `models`, `openreview`, `service`, `settings`, `worker`, and later `observability`.
- `openreview_scraper.service` may import `db`, `models`, `openreview`, and `settings`.
- `openreview_scraper.worker` may import `service`, `db`, and `settings`.
- `openreview_scraper.openreview` may import `models` and `settings`, but not `db`, `service`, `worker`, or `cli`.
- `openreview_scraper.db`, `openreview_scraper.models`, and `openreview_scraper.settings` are leaf layers and must not import CLI or worker modules.
- No module in this repo may import dashboard/UI code from the source repo.

## Enforcement
- `python3 scripts/check_architecture.py` is the current enforcement mechanism.
- `python3 scripts/run_repo_checks.py guardrails` should remain the canonical entrypoint once CI is added.
- If new cross-cutting modules are introduced, update both this document and the script in the same PR.
