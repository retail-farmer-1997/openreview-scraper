# Testing Strategy

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Test Command
- Local and CI baseline: `python3 scripts/run_repo_checks.py tests`
- Packaging release check: `python3 scripts/run_repo_checks.py packaging`

## Scope Baseline
- Bootstrap tests cover repo-local CLI launcher behavior in `tests/test_bootstrap_local_cli.py`.
- CLI contract tests cover the planned command surface in `tests/test_cli_contract.py`.
- Packaging verification workflows should exercise built `sdist` and `wheel` installs plus the
  `openreview-scraper --help`, `--version`, and `db status` smoke path without depending on
  `PYTHONPATH=src`.
- Guardrail coverage is enforced separately by `python3 scripts/check_agent_docs.py` and `python3 scripts/check_architecture.py`.
- Future feature slices must add database, OpenReview client, service, and worker integration tests alongside implementation.

## Determinism Rules
- No test may rely on undeclared external state.
- Prefer local fixtures, stubs, and temporary workspaces.
- Keep setup explicit and reproducible.

## Coverage Target
- Scaffold phase target: keep tests meaningful and focused rather than chasing a numeric threshold.
- Feature phase target: at least 70% line coverage across `src/openreview_scraper` once data, network, and worker modules are implemented.

## Workflow
1. Add or update tests in the same change as behavior changes.
2. Run the baseline test command locally before push.
3. Keep fixtures and mocks minimal, explicit, and local to the tests that use them.
