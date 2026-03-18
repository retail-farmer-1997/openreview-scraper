# PR Loop

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Agent PR Lifecycle
1. Confirm the objective and acceptance criteria from the linked Linear issue or execution plan.
2. Implement the minimal scoped change.
3. Run the required checks locally.
4. Update docs, execution plans, and the linked Linear issue when implementation changes scope or decisions.
5. Open a PR with the linked Linear issue, validation evidence, and risk notes.
6. Address review feedback with focused follow-up commits.
7. Merge only after required checks pass.

## Linear + GitHub Contract
- Every implementation PR should map to one or more Linear issues in the repo's active Linear project.
- Use the Linear issue key in the PR title or body, e.g. `RET-12: finalize package metadata`.
- Keep Linear issue status aligned with actual work state; do not rely on PR draft/open state as an implicit status update.

## Required Local Checks
- `python3 scripts/check_agent_docs.py`
- `python3 scripts/check_architecture.py`
- `python3 scripts/run_repo_checks.py tests`
- Additional lint, type, and security checks once those tools are added to `scripts/run_repo_checks.py`.

## Escalation Conditions
- Unclear product decision that affects behavior.
- Security or privacy concern.
- Data migration risk with potential data loss.
- Repeated flaky failures with no deterministic reproduction.
