# Linear + GitHub Project Operations

## Objective
Replace any implicit Notion-backed project-management behavior for `openreview-scraper` with an
explicit `Linear + GitHub + repo docs` operating model that future agents can follow without
external memory systems.

## Scope
- Add a repo-local `linear-project` skill under `.agents/skills/`.
- Update `AGENTS.md` to declare `Linear + GitHub` as the only accepted project-management surface.
- Update agent-operation docs where the issue/PR loop should explicitly reference Linear.
- Update `agent-harness.json` so the new repo-local skill is part of the enforced docs contract.
- Update the live Linear project/issue text where it still references Notion-backed tracking.

## Risks
- Repo-local workflow guidance can drift from the live Linear project if only one side is updated.
- Over-specifying current issue inventory in repo docs would go stale quickly.
- A new skill without harness enforcement could silently disappear or diverge from `AGENTS.md`.

## Validation
- `python3 /Users/narayansrinivasan/.agents/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/linear-project`
- `python3 scripts/check_agent_docs.py`
- `python3 scripts/check_architecture.py`
- `python3 scripts/run_repo_checks.py tests`

## Status Log
- 2026-03-18: Audited the repo docs and confirmed there were no in-repo Notion references to remove; the migration is primarily an explicit policy clarification plus new repo-local skill support.
- 2026-03-18: Chose `Linear + GitHub + repo docs` as the only project-management contract for this repo and decided to encode it in both `AGENTS.md` and a repo-local `linear-project` skill.
- 2026-03-18: Chose to keep current issue inventory live in Linear rather than duplicating it in repo docs; the repo-local skill will store only stable project identifiers, milestone semantics, and issue/PR conventions.
- 2026-03-18: Added the repo-local `linear-project` skill, linked it from `AGENTS.md`, tightened `docs/agent-ops/pr-loop.md`, and updated `agent-harness.json` so the new workflow contract is enforced by repo checks.
- 2026-03-18: Updated the live Linear project description to declare the `Linear + GitHub` operating model and removed the remaining Notion reference from `RET-8`.
