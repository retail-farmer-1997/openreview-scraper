# Security

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Baseline Security Policy
- Pin and regularly update dependencies.
- Run vulnerability or dependency scans in CI.
- Do not commit secrets.
- Keep external input handling explicit and validated.

## Scan Cadence
- Until CI is wired, dependency additions require explicit review in the PR and a note in the validation section.
- No accepted vulnerability baseline is recorded yet; if one becomes necessary, track it in-repo with a dedicated script and review cadence.
- Once third-party runtime dependencies are added beyond the scaffold, add a dependency-audit command to `scripts/run_repo_checks.py` and make failures block merges.

## Findings Triage
1. Upgrade the direct dependency if a fix is available.
2. If the issue is transitive-only, either upgrade the parent dependency or add a temporary baseline entry with a follow-up issue.
3. Remove temporary exceptions immediately after the fix lands.

## Escalation
- Any suspected data leak or credential exposure should pause merges and trigger focused remediation.
