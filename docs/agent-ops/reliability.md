# Reliability

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Reliability Targets
- External calls use bounded timeouts.
- Retries are limited to transient failures.
- Re-runnable commands remain safe and deterministic.
- Partial failures are recoverable without manual data surgery.

## Operational Checks
- `python3 scripts/run_repo_checks.py tests` must stay green on a clean clone with no pre-existing state.
- `tests/test_bootstrap_local_cli.py` guards the repo-local launcher bootstrap and stale-venv detection flow.
- `tests/test_cli_contract.py` guards the command surface so refactors do not silently drop commands or groups.
- When fetch/download code lands, add rerun tests that confirm idempotent DB/file outcomes before merging.
- When PDF persistence lands, add explicit partial-write/corrupt-file detection tests before enabling background download workers.

## Incident Loop
- Capture the failure mode in an execution plan or incident doc.
- Add a regression test.
- Add a guardrail, lint, metric, or check when possible.
