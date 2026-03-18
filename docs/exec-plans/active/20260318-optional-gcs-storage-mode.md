# Optional GCS Storage Mode

## Objective
Add an explicit optional GCS-backed storage mode alongside the current local-only mode so
`openreview-scraper` can persist its SQLite database snapshot plus downloaded artifacts to the
verified bucket `gs://openreview-scraper-data/` without changing the default local workflow.

## Scope
- Keep `local` as the default mode with no new cloud dependency for existing users.
- Add storage settings for:
  `OPENREVIEW_SCRAPER_STORAGE_MODE=local|gcs-sync`,
  `OPENREVIEW_SCRAPER_GCS_BUCKET`,
  `OPENREVIEW_SCRAPER_GCS_PREFIX`,
  `OPENREVIEW_SCRAPER_GCS_CACHE_DIR`,
  and a small set of sync/lock tunables for long-running worker flows.
- Introduce a storage boundary in a new `src/openreview_scraper/storage.py` module so `cli.py`,
  `service.py`, and `worker.py` talk to a storage runtime instead of assuming local filesystem
  paths everywhere.
- Keep SQLite execution local in both modes. In `gcs-sync` mode, download the database snapshot
  from GCS into a local cache before a command runs, then checkpoint and upload the updated
  snapshot back to GCS after writes.
- Treat the database PDF location field as an artifact locator instead of a guaranteed local path:
  local mode will keep storing absolute filesystem paths, while GCS mode will store `gs://...`
  URIs. Remove direct `Path(...).exists()` assumptions from service code and route those checks
  through the storage layer.
- Store paper PDFs in GCS under a stable object layout:
  `<prefix>/db/openreview-scraper.db`,
  `<prefix>/papers/<paper_id>.pdf`,
  `<prefix>/locks/sqlite-writer.json`.
- Leave room for future non-PDF downloads by reserving `<prefix>/artifacts/` as the generic
  remote artifact root.
- Wrap mutating CLI and worker commands in a single storage session that can:
  1. hydrate the latest DB snapshot,
  2. acquire a GCS-backed write lock,
  3. expose local runtime paths to the existing DB/OpenReview code,
  4. flush DB/artifact state back to GCS on completion.
- Add periodic DB snapshot flushes for long-running worker commands so remote state does not lag
  until process exit.
- Update README and test coverage for both `local` and `gcs-sync` modes.

## Implementation Slices
1. Settings and storage contract
   Add the new storage-mode settings, validate GCS requirements only when `gcs-sync` is selected,
   and define a storage runtime interface that exposes the active local DB path, local cache
   directory for papers, artifact-locator helpers, and lifecycle hooks for sync start/finish.

2. DB snapshot sync
   Build a GCS storage runtime that pulls the DB snapshot into a local cache before command
   execution and uploads it after writes. Before each upload, explicitly checkpoint SQLite WAL
   state so the uploaded `.db` file is complete and does not depend on transient `-wal`/`-shm`
   sidecars.

3. Artifact sync and locator semantics
   Refactor PDF download/reconcile code so it can inspect an existing remote locator, hydrate a
   local cache file from GCS when needed, and upload newly downloaded PDFs to GCS before writing
   the locator back into the DB. Keep checksum and size metadata authoritative in SQLite.

4. Command integration
   Put storage-session orchestration at the CLI/service entrypoint layer so `db.py` remains a
   local SQLite module. Mutating commands should acquire the write lock; read-only commands should
   only hydrate the latest DB snapshot.

5. Worker durability
   Ensure queued worker flows can mark the DB dirty and flush snapshots on a bounded interval or
   completed-job count. Final command exit must always attempt a last checkpoint+upload when work
   succeeded.

6. Documentation and rollout
   Document the two modes, required GCS environment variables, expected bucket layout, and the
   single-writer constraint for `gcs-sync` mode. Keep the initial rollout explicitly opt-in.

## Risks
- SQLite cannot run safely from GCS object storage directly; the only viable approach here is a
  synchronized local working copy. Trying to treat GCS like a shared POSIX filesystem would create
  silent corruption risk.
- The repo currently assumes `pdf_path` is a local path in multiple places. Missing one of those
  call sites would create false “missing file” downloads or broken idempotency.
- SQLite WAL mode is enabled by default today. Uploading only the main `.db` file without a
  checkpoint would lose recent writes.
- Multiple concurrent writers in `gcs-sync` mode can overwrite each other unless the runtime
  enforces a lock plus generation-aware upload semantics.
- Long-running workers can generate too much sync traffic if DB uploads are too frequent, or lose
  too much state on crash if uploads are too sparse.
- If GCS mode shells out to `gcloud storage`, the runtime will depend on `gcloud` being installed
  and authenticated. If it uses the Python client instead, credentials will need to be wired
  cleanly for non-interactive environments. Pick one adapter and isolate it behind `storage.py`.

## Validation
- Add settings tests that cover `local` defaults, `gcs-sync` validation, and cache-path behavior.
- Add storage-runtime tests for DB hydrate/upload, lock acquisition/release, and artifact-locator
  resolution.
- Add service/worker tests that prove a `gs://...` PDF locator does not trigger a redundant
  OpenReview download when the object already exists in GCS.
- Add CLI integration coverage for a representative read-only command and a representative mutating
  command in `gcs-sync` mode using a fake storage adapter.
- Run:
  `uv run python scripts/run_repo_checks.py all`

## Status Log
- 2026-03-18: Verified the signed-in GCP account as `flyinpenguin669@gmail.com`, the active
  project as `openclaw-gateway-prod-00970`, and the available bucket as
  `gs://openreview-scraper-data/` in `EUROPE-WEST2`.
- 2026-03-18: Confirmed the current codebase is local-path-centric: `settings.py` exposes only
  `db_path` and `papers_dir`, `db.py` opens a local SQLite file, and `service.py` treats
  `papers.pdf_path` as a filesystem path.
- 2026-03-18: Chose to keep SQLite local in both modes and make GCS a synchronized persistence
  layer, not a direct database runtime.
- 2026-03-18: Chose a dedicated `storage.py` boundary so CLI/service/worker layers can opt into
  GCS sync without spreading bucket logic through `db.py` and `openreview.py`.
- 2026-03-18: Locked the first implementation goal to an explicit opt-in `gcs-sync` mode with a
  single-writer guarantee; default local behavior remains unchanged.
- 2026-03-18: Implemented the first storage-contract slice: `settings.py` now models local vs
  `gcs-sync` storage settings and tunables, `storage.py` defines local and GCS runtime/layout
  objects plus locator-to-cache-path helpers, and the architecture guardrails were updated to
  recognize the new boundary.
- 2026-03-18: Kept the initial `storage.py` session lifecycle hooks as no-op groundwork so the
  repo can land the runtime contract and tests before the next slices add DB hydrate/upload,
  locking, and CLI/worker orchestration.
