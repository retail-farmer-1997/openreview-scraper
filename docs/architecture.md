# Architecture

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

Canonical source: root-level `ARCHITECTURE.md`.

## Summary
- The repo is intentionally CLI-only: command parsing in `cli.py`, business orchestration in `service.py`, and no dashboard/runtime UI package.
- External I/O is split cleanly between `openreview.py` for network access and `db.py` for local persistence.
- `models.py` and `settings.py` are the stability anchors; they should remain small, typed, and reusable across command flows.

## Dependency Direction
- Presentation flows inward: `__main__ -> cli -> service/openreview/db/settings/models/worker`.
- Leaf modules do not reach back upward: `models.py`, `settings.py`, and eventually `observability.py` stay independent from CLI and worker orchestration.
