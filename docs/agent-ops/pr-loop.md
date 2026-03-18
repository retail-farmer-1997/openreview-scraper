# PR Loop

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Agent PR Lifecycle
1. Confirm the objective and acceptance criteria from the issue or plan.
2. Implement the minimal scoped change.
3. Run the required checks locally.
4. Update docs and plans that changed due to the implementation.
5. Open a PR with validation evidence and risk notes.
6. Address review feedback with focused follow-up commits.
7. Merge only after required checks pass.

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
