# Adaptive Rate-Limit Pausing

## Objective
Upgrade the OpenReview request throttle so the scraper does not only wait for a single 429 reset
window, but also learns from repeated rate-limit responses and reduces its ongoing request pace
until the server stops pushing back.

## Scope
- Keep all rate-limit control inside `src/openreview_scraper/openreview.py` so the adaptive policy
  stays at the network boundary.
- Preserve the existing explicit reset-window / `Retry-After` handling as a hard lower bound for
  retries.
- Add an adaptive throughput controller that increases inter-request spacing when 429s happen and
  gradually recovers after successful requests.
- Extend request observability and regression coverage so worker status reflects the live throttle
  state without changing the CLI/service/worker boundaries.
- Update README language to document the smarter adaptive behavior.

## Design
- Use additive-increase / multiplicative-decrease behavior expressed as a request-interval
  controller:
  - on rate-limit events, multiply the effective minimum request interval,
  - on successful responses, shrink that interval back toward the configured baseline in small
    additive steps.
- Continue honoring server-provided reset windows as the primary pause signal.
- Keep the controller process-local so all local worker threads share the same learned slowdown.

## Risks
- Over-aggressive multiplicative slowdown can make long queue drains unnecessarily slow after a
  single burst of 429s.
- Recovery that is too fast will oscillate and recreate the same rate-limit pattern.
- New observability fields must remain additive so existing CLI/test consumers keep working.

## Validation
- `uv run python scripts/run_repo_checks.py all`

## Status Log
- 2026-03-18: Confirmed the repo already has a process-local request throttle plus explicit
  reset-window waits for 429s, but it does not learn from repeated rate-limit responses and keeps
  using the same baseline spacing after the wait expires.
- 2026-03-18: Chose an AIMD-style interval controller at the OpenReview boundary so local worker
  threads share one learned slowdown without introducing queue/worker-specific throttling logic.
- 2026-03-18: Implemented the adaptive controller in `openreview.py`: successful responses now
  shrink the learned interval in additive steps, repeated 429s multiply the interval upward, and
  request metrics expose the effective interval plus adaptive-slowdown state.
- 2026-03-18: Added regression coverage for interval adaptation/recovery, adaptive spacing after a
  reset-window pause, and HTTP-date `Retry-After` parsing; updated README to describe the smarter
  queue-drain behavior.
- 2026-03-18: Validation passed with `uv run python scripts/check_agent_docs.py`, `uv run python
  scripts/check_architecture.py`, and `uv run python scripts/run_repo_checks.py tests`.
