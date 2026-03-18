# Parallel Local Download Drain

## Objective
Add a single local CLI path that can queue all papers needing PDF work from the SQLite database,
drain that queue with multiple local workers, and surface progress while the queue is being
processed.

## Scope
- Extend the existing `worker run-downloads` command rather than adding a second queue runner.
- Keep queue ownership in `db.py`, download behavior in `service.py`, and orchestration in
  `worker.py`.
- Add tests for the new worker orchestration and CLI output/JSON behavior.
- Update repo docs to show the local batch-download workflow after `fetch`.

## Risks
- Parallel workers increase SQLite contention if the queue runner claims jobs too aggressively.
- Human-readable monitoring output must not break the existing JSON-only automation path.
- The queue runner must preserve idempotent behavior when papers are already downloaded or already
  queued.

## Validation
- `python3 scripts/check_agent_docs.py`
- `python3 scripts/check_architecture.py`
- `python3 scripts/run_repo_checks.py tests`

## Status Log
- 2026-03-18: Confirmed the current repo already has queued download jobs plus a single-worker
  `worker run-downloads` flow, but no one-shot local command for queueing missing papers, running
  multiple workers, and monitoring progress.
- 2026-03-18: Chose to extend the existing `worker run-downloads` command with
  `--enqueue-missing`, `--workers`, and live status reporting instead of adding a second batch
  download command.
- 2026-03-18: Implemented parallel local download draining in `worker.py` with a bounded thread
  pool over the existing queue-claim path so queue ownership and idempotent download behavior stay
  in the current DB/service layers.
- 2026-03-18: Added worker and CLI regression coverage for queue reconciliation, multi-worker
  draining, and human-readable status output; updated README with the post-fetch batch download
  workflow.
- 2026-03-18: Upgraded `worker run-downloads` from count-only status lines to streamed per-paper
  progress events plus an interactive terminal dashboard with active paper titles, worker-slot
  progress bars, and aggregate throughput/constraint reporting.
- 2026-03-18: Validation passed for the richer runner with `uv run python
  scripts/check_agent_docs.py`, `uv run python scripts/check_architecture.py`, and `uv run python
  scripts/run_repo_checks.py tests`.
