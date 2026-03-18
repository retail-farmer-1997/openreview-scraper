# AGENTS.md

This repository is operated in agent-first mode: humans define outcomes, constraints, and
priorities; agents execute implementation.

## Start Here
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for domain boundaries.
- Read [README.md](README.md) for the current repo scope and bootstrap flow.
- Read [docs/agent-ops/index.md](docs/agent-ops/index.md) for the operating model.
- Read [docs/agent-ops/pr-loop.md](docs/agent-ops/pr-loop.md) before opening PRs.
- Read [docs/exec-plans/tech-debt-tracker.md](docs/exec-plans/tech-debt-tracker.md) before large refactors.

## Operating Rules
- Keep changes small and independently mergeable.
- Prefer explicit tests over assumptions.
- Update docs and execution plans in the same PR as behavior changes.
- Do not add hidden context; decisions must be recorded in-repo.
- Enforce invariants mechanically where possible.

## Required Artifacts For Non-Trivial Changes
- An execution plan in `docs/exec-plans/active/`.
- A status update in the plan after each material decision.
- A completed plan moved to `docs/exec-plans/completed/` at merge.

## Golden Principles
- Validate data at boundaries.
- Prefer deterministic behavior and idempotent operations.
- Keep modules small and legible.
- Keep the CLI/backend boundary clean; do not reintroduce dashboard code here.
- Optimize for agent legibility over stylistic novelty.

## Quality Gate
Run:

```bash
python3 scripts/check_agent_docs.py
python3 scripts/check_architecture.py
python3 scripts/run_repo_checks.py tests
```

If the repo layout changes, update `agent-harness.json` so the docs contract stays accurate.
