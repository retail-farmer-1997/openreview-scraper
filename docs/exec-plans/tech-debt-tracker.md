# Tech Debt Tracker

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Active Debt
- CI is not wired yet; local guardrail and test entrypoints exist but are not enforced remotely.
- Legacy `RESEARCH_*` environment-variable compatibility is still enabled; remove it after downstream callers migrate to `OPENREVIEW_SCRAPER_*`.
- Lint, type-check, and dependency-audit tooling still need to be wired now that the runtime port introduced third-party dependencies.

## Update Policy
- Every merged PR that introduces debt must add an entry.
- Every debt-removal PR must remove or reduce an entry.
