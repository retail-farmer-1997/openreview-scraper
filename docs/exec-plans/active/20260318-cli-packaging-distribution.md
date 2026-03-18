# CLI Packaging And Distribution

## Objective
Make `openreview-scraper` publishable as a Python CLI package and installable via `pipx`/`pip`
without losing the repo-local `./openreview-scraper` bootstrap flow or the current CLI/backend
architecture boundaries.

## Scope
- Harden installed-package runtime behavior so wheels/sdists do not depend on repo-relative paths
  or source-checkout assumptions.
- Ensure bundled runtime assets such as SQL migrations are shipped and load correctly from built
  artifacts.
- Promote `pyproject.toml` from minimal source-install metadata to publishable package metadata.
- Add deterministic build and artifact-verification commands for `sdist` and `wheel`.
- Add offline smoke coverage that installs built artifacts into an isolated environment and verifies
  the CLI contract.
- Add maintainer docs and release automation for packaging, tagging, and publishing.
- Map implementation slices to a parent Linear issue plus child issues in the `Packaging and
  Distribution` milestone before code work starts.

## Non-Goals
- Shipping standalone binaries or non-Python distribution channels in this pass.
- Reworking the CLI command surface beyond packaging-driven fixes.
- Changing runtime defaults unless installed-package validation proves the current behavior is
  incorrect.
- Mixing packaging work with unrelated reliability or feature changes.

## Risks
- Installed packages cannot rely on repo-root assumptions; `settings.py` currently resolves
  relative paths from the source checkout.
- Database migrations are loaded from package-adjacent SQL files, so package-data handling must be
  explicit and verified for both wheel and sdist installs.
- The current test suite exercises source and editable-install paths, not isolated built-artifact
  installs, so packaging regressions can slip through without new artifact smoke coverage.
- No build or publish workflow exists yet, which raises the risk of manual drift and inconsistent
  release steps.
- Version metadata can drift between `pyproject.toml`, `__init__.py`, and release tags unless the
  package adopts a single-source version contract.
- Packaging changes must preserve the existing contributor bootstrap path so local development does
  not regress while end-user installation improves.

## Validation
- `uv run python scripts/check_agent_docs.py`
- `uv run python scripts/check_architecture.py`
- `uv run python scripts/run_repo_checks.py tests`
- `uv run python scripts/run_repo_checks.py packaging`
- `uv run python scripts/run_packaging_smoke.py --dist-dir dist`
- New installed-artifact smoke validation that creates a clean environment, installs the built
  package, and verifies `openreview-scraper --help`, `--version`, and `db status` work without
  repo-relative dependencies
- Keep authenticated OpenReview smoke checks separate from publish gating so packaging validation
  stays offline and deterministic

## Dependency-Ordered Task List

### Parallelizable Now
1. Installed-package runtime hardening
   - Replace repo-checkout assumptions for runtime assets with installed-package-safe loading,
     including SQL migrations.
   - Revisit relative-path resolution so explicit relative env values resolve from a documented user
     context rather than `site-packages` or the repo root.
   - Add focused tests that fail when runtime assets are missing from built artifacts.
2. Publishable metadata and version contract
   - Fill out `pyproject.toml` metadata needed for public distribution: license, project URLs,
     classifiers, keywords, maintainer/author fields, and any packaging-specific README details.
   - Choose a single source of truth for the package version so CLI output, built artifacts, and
     release tags stay aligned.
   - Add user-facing install, upgrade, and uninstall docs for `pipx` and `pip install`.

### Depends On Foundations Above
3. Build tooling and artifact verification
   - Add repo-local tooling and commands to build `sdist` and `wheel` artifacts reproducibly.
   - Add packaging smoke checks that install built artifacts into isolated environments and exercise
     the CLI without `PYTHONPATH=src` or editable-install assumptions.
   - Verify both wheel and sdist install paths, not just the repo bootstrap path.
4. Release automation
   - Add GitHub Actions for build verification on PRs and artifact publication on tagged releases
     or explicit release dispatch.
   - Prefer PyPI trusted publishing, or the repo-approved equivalent, over long-lived publish
     tokens; document the credential and permission contract in-repo.
   - Define tag naming, release notes, rollback, and re-release behavior so maintainers do not
     improvise production release steps.

### Final Integration And Hardening
5. Maintainer workflow and rollout
   - Document how contributors continue using `./openreview-scraper` locally while end users
     install via `pipx install openreview-scraper`.
   - Add a concise release checklist and post-publish smoke steps.
   - Split follow-on distribution channels into separate backlog items instead of expanding this
     slice.

## Status Log
- 2026-03-18: Audited repo docs and confirmed packaging/distribution belongs in the repo's
  `Packaging and Distribution` milestone.
- 2026-03-18: Confirmed the repo is already source-installable via `pyproject.toml` and the
  `openreview-scraper` console script, but it does not yet have any build, publish, or release
  automation workflow.
- 2026-03-18: Confirmed `uv run python -m build --help` fails because the `build` package is not
  present in the current toolchain, so this work must add explicit build-tool support instead of
  assuming it already exists.
- 2026-03-18: Identified two installed-artifact risks that must be closed before publication:
  `db.py` loads migrations from package-adjacent SQL files, and `settings.py` anchors relative-path
  resolution to the source checkout.
- 2026-03-18: Added PR verification and release publication workflows plus maintainer release docs;
  source-side runtime hardening and version-contract changes remain for the follow-up packaging
  implementation slice.
- 2026-03-18: Added workflow checks that compare `pyproject.toml` against the CLI version constant
  and reject release tags that do not match the packaged version.
- 2026-03-18: Switched the package version contract to a single source of truth in
  `src/openreview_scraper/__init__.py`, with `pyproject.toml` reading that value dynamically for
  builds and release checks.
- 2026-03-18: Hardened installed-package runtime behavior by resolving explicit relative data paths
  from the caller's current working directory instead of the source checkout.
- 2026-03-18: Reworked migration discovery to use package-resource loading, added explicit package
  data for SQL migrations, and added build-tool dependency locking plus deterministic artifact smoke
  checks.
- 2026-03-18: Documented end-user `pipx`/`pip` install flows and updated release automation to run
  the repo-local packaging smoke script with the locked packaging toolchain.
