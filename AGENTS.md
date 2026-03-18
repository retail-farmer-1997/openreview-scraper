# AGENTS.md

This repository is operated in agent-first mode: humans define outcomes, constraints, and
priorities; agents execute implementation.

## Start Here
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for domain boundaries.
- Read [README.md](README.md) for the current repo scope and bootstrap flow.
- Read [docs/agent-ops/index.md](docs/agent-ops/index.md) for the operating model.
- Read [docs/agent-ops/pr-loop.md](docs/agent-ops/pr-loop.md) before opening PRs.
- Read [docs/exec-plans/tech-debt-tracker.md](docs/exec-plans/tech-debt-tracker.md) before large refactors.
- Read [.agents/skills/linear-project/SKILL.md](.agents/skills/linear-project/SKILL.md) when the task touches Linear issues, milestones, project status, release planning, or GitHub/PR linkage.

## Project-Management Source Of Truth
- Use Linear + GitHub only for this repo.
- Linear project `OpenReview Scraper` in team `Retail-farmer` owns task state, milestones, priorities, and dependencies.
- GitHub repo `retail-farmer-1997/openreview-scraper` owns code, branches, PR review, and merged history.
- Repo docs (`AGENTS.md`, `docs/agent-ops/*`, and `docs/exec-plans/*`) own durable operating instructions and in-flight decision logs.
- Do not use Notion or other external memory systems for this repo.

## Operating Rules
- Keep changes small and independently mergeable.
- Prefer explicit tests over assumptions.
- Update docs and execution plans in the same PR as behavior changes.
- Do not add hidden context; decisions must be recorded in-repo.
- Enforce invariants mechanically where possible.
- Use `uv run` / `uv run python` for repo-local Python commands so tooling resolves against the pinned env.
- Start non-trivial implementation from a Linear issue or an explicitly referenced execution plan.
- Keep the Linear issue, branch/PR linkage, and active execution plan aligned when scope or decisions change.

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
uv run python scripts/check_agent_docs.py
uv run python scripts/check_architecture.py
uv run python scripts/run_repo_checks.py tests
```

If the repo layout changes, update `agent-harness.json` so the docs contract stays accurate.
