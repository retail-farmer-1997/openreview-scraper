# Packaging And Release Process

Owner: retail-farmer-1997
Last Reviewed: 2026-03-18
Status: Active

## Purpose
This repository ships `openreview-scraper` as a Python CLI package while keeping the repo-local
`./openreview-scraper` bootstrap path available for contributors.

## Workflow Files
- `.github/workflows/package-verification.yml` checks pull requests and manual runs by running
  `uv run python scripts/run_repo_checks.py all`, which includes the docs guardrails, test suite,
  and packaging smoke flow.
- `.github/workflows/release-publish.yml` handles tag-driven publication with trusted publishing.

## Canonical Validation Command
- `uv run python scripts/run_repo_checks.py all` is the repo-local validation gate for PRs and release verification.

## Tagging Rules
- Cut releases from a merged commit on `main`.
- Use `vX.Y.Z` tags.
- Keep the tag aligned with `src/openreview_scraper/__init__.py`; `pyproject.toml` imports the
  package version dynamically from that constant.
- Do not publish from an unmerged branch or from a tag that does not match the package version.

## Release Flow
1. Merge the release-ready change set.
2. Confirm the version bump is in place.
3. Create and push the `vX.Y.Z` tag.
4. The release workflow builds `sdist` and `wheel`, verifies distribution metadata, smoke-tests
   fresh installs, and publishes to PyPI through trusted publishing.
5. Record the release notes in GitHub after publish.

Manual publication is also available through the workflow dispatch path, but it must point at an
existing `vX.Y.Z` tag rather than a branch ref.

## Trusted Publishing Assumptions
- PyPI is configured with a trusted publisher entry for this GitHub repository.
- The publish job uses GitHub OIDC and does not rely on a long-lived PyPI API token.
- The release workflow only publishes for a `v*` tag push or an explicit manual dispatch that names
  an existing `v*` tag and opts into publication.

## Post-Publish Smoke Checks
- Install the released version into a fresh environment with `pipx install openreview-scraper==X.Y.Z`
  or `python -m pip install openreview-scraper==X.Y.Z`.
- Run `openreview-scraper --help`, `openreview-scraper --version`, and `openreview-scraper db status`.
- Point `OPENREVIEW_SCRAPER_DB_PATH` and `OPENREVIEW_SCRAPER_PAPERS_DIR` at temporary locations so
  the smoke run does not touch a real workspace.
- If post-publish smoke fails, yank the release and cut a replacement tag instead of overwriting the
  release record in place.

## Related
- [Packaging and Distribution Plan](exec-plans/active/20260318-cli-packaging-distribution.md)
- [Testing Strategy](agent-ops/testing.md)
