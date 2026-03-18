# Linear Issue Patterns

## Status Flow
- `Backlog`: parked or not ready for near-term execution.
- `Todo`: ready to be picked up.
- `In Progress`: active implementation or investigation is underway.
- `In Review`: an open PR or equivalent review step is in flight.
- `Done`: merged or otherwise fully completed.
- `Canceled` / `Duplicate`: explicitly closed without delivery.

## Issue Shape
- Use one issue per meaningful work unit.
- Use a parent issue for a multi-step outcome and child issues for decomposed slices.
- Use `blockedBy` only for real sequencing constraints, not soft preferences.
- Put acceptance criteria in the issue description, not only in a PR.
- Update the issue description when scope changes materially.

## Milestones
- Use milestones for delivery phases, not microtasks.
- Prefer putting detail in issues rather than expanding milestone descriptions.
- Create a new milestone only when the phase is durable enough to group multiple issues.

## GitHub Contract
- Prefer the Linear-provided `gitBranchName` when it exists on the issue.
- Otherwise include the Linear issue key in the branch name.
- Start PR titles with the issue key, e.g. `RET-12: finalize package metadata`.
- Include `Closes RET-xx` or `Refs RET-xx` in the PR body.
- Record validation commands and risk notes in the PR body.

## Repo Contract
- Non-trivial implementation work requires an active execution plan under `docs/exec-plans/active/`.
- Update the plan whenever a material decision or scope shift happens.
- Record durable workflow changes in repo docs rather than external systems.
