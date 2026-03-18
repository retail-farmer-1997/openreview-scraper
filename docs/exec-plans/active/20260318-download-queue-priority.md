# Download Queue Priority

## Objective
Add first-class download queue priority so queued PDF jobs always drain in this order:
oral, then spotlight, then poster, then everything else.

## Scope
- Add persisted priority data for `download_jobs`.
- Derive queue priority from stored paper venue metadata.
- Apply the priority consistently to reconcile enqueue ordering and worker claim ordering.
- Cover the behavior with migration and DB integration tests.
- Document the queue ordering in repo docs.

## Risks
- `enqueue-downloads --limit` currently slices candidates before enqueue, so priority must be applied before the limit or the wrong papers will enter the queue.
- Existing databases may already contain pending/running jobs, so the migration must backfill priority for old rows instead of only affecting new jobs.
- Manual `enqueue-download <paper_id>` calls can target papers that are not yet in the DB; those jobs need a deterministic fallback priority.

## Validation
- `uv run python -m pytest tests/test_migrations.py tests/test_db_integration.py tests/test_cli_integration.py`
- `uv run python scripts/run_repo_checks.py all`

## Status Log
- 2026-03-18: Confirmed the existing queue is FIFO only: reconcile candidates are ordered by paper `created_at` and download workers claim jobs by job `created_at`.
- 2026-03-18: Chose a persisted `download_priority` rank on `download_jobs` so old queued rows can be backfilled during migration and worker ordering does not depend on ad hoc CLI workflows.
- 2026-03-18: Implemented migration `0007_download_priority.sql`, backfilled existing queue rows from paper venue metadata, and updated enqueue/claim ordering so both queue population and worker drains honor `oral > spotlight > poster > other`.
- 2026-03-18: Added migration and DB integration coverage for the new priority rank, including regressions for `enqueue-downloads --limit` and for claiming a newer oral job ahead of an older poster job.
