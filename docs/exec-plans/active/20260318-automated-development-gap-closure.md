# Automated Development Gap Closure

## Objective
Close the remaining repository-harness gaps identified by the `automated-development` audit so
`openreview-scraper` has one coherent agent-first operating model, one canonical validation
entrypoint, and remote enforcement for the same lint, type, security, and packaging checks that
local contributors are expected to run.

## Scope
- Add missing repo-local validation surfaces for linting, static typing, and dependency/security
  auditing.
- Wire those checks into `scripts/run_repo_checks.py`, `pyproject.toml`, and GitHub Actions so the
  PR/release gate matches the repo-local contract.
- Align architecture docs and enforcement with the actual module boundaries in `ARCHITECTURE.md`,
  including the `storage.py` dependency edge and the canonical `uv run` validation flow.
- Update testing, security, quality, PR-template, and tech-debt docs so they describe the final
  enforced toolchain instead of transitional debt.
- Remove or reduce the matching entries from `docs/exec-plans/tech-debt-tracker.md` when the new
  checks land.

## Non-Goals
- Reworking core CLI, DB, worker, or OpenReview behavior outside what is required to satisfy the
  new validation tools.
- Removing legacy `RESEARCH_*` compatibility in this slice unless the required downstream migration
  evidence is already available.
- Introducing a large multi-language or monorepo-wide policy scaffold beyond the current Python CLI
  repository.

## Risks
- Adding lint, type, and dependency-audit tooling will surface real existing issues; this can turn
  a “tooling” slice into a broader cleanup unless the scope stays disciplined.
- Static typing against third-party libraries such as `openreview-py` may require explicit stubs,
  narrowed coverage, or temporary ignores to avoid blocking progress on noise.
- Dependency-audit tooling can fail on transitive advisories with no immediate fix; the repo needs a
  durable, explicit baseline/exception policy rather than ad hoc suppression.
- Architecture-doc changes that do not match `scripts/check_architecture.py` will reintroduce the
  same drift the harness is meant to prevent.
- Expanding the CI gate will increase PR latency; the command graph in `scripts/run_repo_checks.py`
  should stay explicit and debuggable.

## Validation
- Baseline before changes: `uv run python scripts/run_repo_checks.py all`
- New repo-local toolchain command after implementation:
  `uv run python scripts/run_repo_checks.py all`
  where `all` expands to guardrails, lint, types, tests, packaging, and dependency/security audit.
- Focused command slices while iterating:
  `uv run python scripts/run_repo_checks.py guardrails`
  `uv run python scripts/run_repo_checks.py lint`
  `uv run python scripts/run_repo_checks.py types`
  `uv run python scripts/run_repo_checks.py tests`
  `uv run python scripts/run_repo_checks.py packaging`
  `uv run python scripts/run_repo_checks.py audit`
- `uv lock` after adding the new validation toolchain dependencies.
- GitHub Actions `package-verification` workflow green on the expanded repo-local validation gate.
- GitHub Actions `release-publish` verification job green on the expanded repo-local validation
  gate.

## Dependency-Ordered Task List

### Parallelizable Now
1. Validation toolchain design
   - Choose the smallest repo-local tools that close the documented gaps:
     `ruff` for lint, `mypy` for static typing, and `pip-audit` for dependency/security audit unless
     repo constraints force a better equivalent.
   - Decide the initial enforcement scope for each tool so the first version is strict but
     realistic, for example `src/openreview_scraper` plus selected `scripts/`.
   - Add dependency groups and tool configuration to `pyproject.toml`.

2. Architecture-contract convergence
   - Align `ARCHITECTURE.md`, `docs/agent-ops/architecture-invariants.md`, and
     `scripts/check_architecture.py` so the documented dependency graph matches actual allowed
     imports, including `storage.py` as a first-class boundary.
   - Normalize the “enforced dependency rules” language onto the canonical `uv run` commands.

### Depends On Validation Tool Choices
3. Repo-local enforcement wiring
   - Extend `scripts/run_repo_checks.py` with `lint`, `types`, and `audit` commands.
   - Add dedicated helper scripts only if the command line would otherwise become opaque or
     repetitive.
   - Make `all` expand to the full validation gate without hiding command order.

4. Codebase cleanup to satisfy the new tools
   - Fix Ruff violations, typing failures, and dependency-audit findings surfaced by the chosen
     tools.
   - If a finding cannot be fixed immediately, record the temporary exception in-repo with explicit
     scope, owner, and exit criteria instead of leaving it as shell-only suppression.

### Depends On Repo-Local Enforcement
5. CI and template alignment
   - Update `.github/workflows/package-verification.yml` and `.github/workflows/release-publish.yml`
     so they execute the same expanded `run_repo_checks.py` entrypoint.
   - Update `.github/pull_request_template.md`, testing/security docs, and any release docs so
     contributors see the same command set that CI runs.
   - Add or update artifact upload steps only if they still provide release value after the gate is
     centralized.

### Final Integration And Debt Retirement
6. Tracker and quality cleanup
   - Remove or reduce the matching lint/type/audit/CI debt entries from
     `docs/exec-plans/tech-debt-tracker.md`.
   - Re-score `docs/agent-ops/quality-score.md` if the repo materially improves its CI/CD or
     security maturity.
   - Update this plan’s status log after each material tool or policy decision.

## Status Log
- 2026-03-18: Audited the repo against the `automated-development` harness model and confirmed the
  remaining gaps are now concentrated in validation/tooling rather than missing base scaffold
  files.
- 2026-03-18: Confirmed the current canonical gate (`uv run python scripts/run_repo_checks.py all`)
  covers guardrails, tests, and packaging smoke, but not linting, static typing, or dependency
  audit.
- 2026-03-18: Identified architecture-contract drift that should be closed in the same slice:
  `ARCHITECTURE.md` and `docs/agent-ops/architecture-invariants.md` still disagree about the
  `storage.py` dependency edges and some command wording.
- 2026-03-18: Confirmed the active tech-debt tracker still lists the missing lint/type/dependency
  audit gate as the highest-value remaining harness gap after packaging/release automation landed.
