"""Storage runtime selection and artifact locator helpers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol
from urllib.parse import urlparse

from . import settings as settings_module
from .settings import Settings


LOCAL_STORAGE_MODE = "local"
GCS_SYNC_STORAGE_MODE = "gcs-sync"
DB_OBJECT_PATH = "db/openreview-scraper.db"
PAPERS_OBJECT_PREFIX = "papers"
ARTIFACTS_OBJECT_PREFIX = "artifacts"
LOCK_OBJECT_PATH = "locks/sqlite-writer.json"


class StorageRuntime(Protocol):
    """Common storage contract for local and remote-backed command runtimes."""

    storage_mode: str
    local_db_path: Path
    local_papers_dir: Path
    storage_sync_interval_seconds: float
    storage_flush_after_jobs: int
    storage_lock_timeout_seconds: float
    storage_lock_poll_interval_seconds: float

    def start_session(self, *, writable: bool) -> None:
        """Prepare storage state before a command starts."""

    def finish_session(self, *, writable: bool, success: bool) -> None:
        """Flush and release storage state when a command finishes."""

    def paper_cache_path(self, paper_id: str) -> Path:
        """Return the active local cache file for a paper PDF."""

    def paper_locator(self, paper_id: str) -> str:
        """Return the persisted locator for a paper PDF."""

    def cache_path_for_locator(self, locator: str) -> Path | None:
        """Resolve a persisted locator into a local cache path when possible."""

    def locator_uses_remote_storage(self, locator: str | None) -> bool:
        """Return whether a locator points at remote storage."""


@dataclass(frozen=True)
class LocalStorageRuntime:
    """Local-only storage runtime with no sync lifecycle work."""

    local_db_path: Path
    local_papers_dir: Path
    storage_sync_interval_seconds: float
    storage_flush_after_jobs: int
    storage_lock_timeout_seconds: float
    storage_lock_poll_interval_seconds: float
    storage_mode: str = LOCAL_STORAGE_MODE

    def start_session(self, *, writable: bool) -> None:
        del writable

    def finish_session(self, *, writable: bool, success: bool) -> None:
        del writable, success

    def paper_cache_path(self, paper_id: str) -> Path:
        return self.local_papers_dir / f"{paper_id}.pdf"

    def paper_locator(self, paper_id: str) -> str:
        return str(self.paper_cache_path(paper_id))

    def cache_path_for_locator(self, locator: str) -> Path | None:
        if urlparse(locator).scheme == "gs":
            return None
        return Path(locator)

    def locator_uses_remote_storage(self, locator: str | None) -> bool:
        if not locator:
            return False
        return urlparse(locator).scheme == "gs"


@dataclass(frozen=True)
class GcsSyncStorageRuntime:
    """GCS-backed storage layout plus local cache paths for runtime execution."""

    gcs_bucket: str
    gcs_prefix: str
    gcs_cache_dir: Path
    local_db_path: Path
    local_papers_dir: Path
    remote_db_uri: str
    remote_papers_uri_prefix: str
    remote_artifacts_uri_prefix: str
    writer_lock_uri: str
    storage_sync_interval_seconds: float
    storage_flush_after_jobs: int
    storage_lock_timeout_seconds: float
    storage_lock_poll_interval_seconds: float
    storage_mode: str = GCS_SYNC_STORAGE_MODE

    def start_session(self, *, writable: bool) -> None:
        del writable

    def finish_session(self, *, writable: bool, success: bool) -> None:
        del writable, success

    def paper_cache_path(self, paper_id: str) -> Path:
        return self.local_papers_dir / f"{paper_id}.pdf"

    def paper_locator(self, paper_id: str) -> str:
        return _join_gcs_uri(self.gcs_bucket, self.gcs_prefix, PAPERS_OBJECT_PREFIX, f"{paper_id}.pdf")

    def cache_path_for_locator(self, locator: str) -> Path | None:
        parsed = urlparse(locator)
        if parsed.scheme != "gs":
            return Path(locator)
        if parsed.netloc != self.gcs_bucket:
            return None

        object_path = parsed.path.lstrip("/")
        prefix = f"{self.gcs_prefix}/" if self.gcs_prefix else ""
        if prefix and not object_path.startswith(prefix):
            return None
        relative_path = object_path[len(prefix):] if prefix else object_path

        if relative_path == DB_OBJECT_PATH:
            return self.local_db_path
        if relative_path.startswith(f"{PAPERS_OBJECT_PREFIX}/"):
            suffix = relative_path.removeprefix(f"{PAPERS_OBJECT_PREFIX}/")
            return self.local_papers_dir / suffix
        if relative_path.startswith(f"{ARTIFACTS_OBJECT_PREFIX}/"):
            return self.gcs_cache_dir / relative_path
        if relative_path == LOCK_OBJECT_PATH:
            return self.gcs_cache_dir / LOCK_OBJECT_PATH
        return None

    def locator_uses_remote_storage(self, locator: str | None) -> bool:
        if not locator:
            return False
        return urlparse(locator).scheme == "gs"


def _join_gcs_uri(bucket: str, *parts: str) -> str:
    cleaned = [part.strip("/") for part in parts if part.strip("/")]
    if not cleaned:
        return f"gs://{bucket}"
    return f"gs://{bucket}/{'/'.join(cleaned)}"


def build_storage_runtime(runtime_settings: Settings | None = None) -> StorageRuntime:
    """Build the active storage runtime from settings."""
    resolved = settings_module.get_settings() if runtime_settings is None else runtime_settings
    if resolved.storage_mode == LOCAL_STORAGE_MODE:
        return LocalStorageRuntime(
            local_db_path=resolved.db_path,
            local_papers_dir=resolved.papers_dir,
            storage_sync_interval_seconds=resolved.storage_sync_interval_seconds,
            storage_flush_after_jobs=resolved.storage_flush_after_jobs,
            storage_lock_timeout_seconds=resolved.storage_lock_timeout_seconds,
            storage_lock_poll_interval_seconds=resolved.storage_lock_poll_interval_seconds,
        )

    return GcsSyncStorageRuntime(
        gcs_bucket=str(resolved.gcs_bucket),
        gcs_prefix=resolved.gcs_prefix,
        gcs_cache_dir=resolved.gcs_cache_dir,
        local_db_path=resolved.gcs_cache_dir / DB_OBJECT_PATH,
        local_papers_dir=resolved.gcs_cache_dir / PAPERS_OBJECT_PREFIX,
        remote_db_uri=_join_gcs_uri(str(resolved.gcs_bucket), resolved.gcs_prefix, DB_OBJECT_PATH),
        remote_papers_uri_prefix=_join_gcs_uri(
            str(resolved.gcs_bucket), resolved.gcs_prefix, PAPERS_OBJECT_PREFIX
        ),
        remote_artifacts_uri_prefix=_join_gcs_uri(
            str(resolved.gcs_bucket), resolved.gcs_prefix, ARTIFACTS_OBJECT_PREFIX
        ),
        writer_lock_uri=_join_gcs_uri(str(resolved.gcs_bucket), resolved.gcs_prefix, LOCK_OBJECT_PATH),
        storage_sync_interval_seconds=resolved.storage_sync_interval_seconds,
        storage_flush_after_jobs=resolved.storage_flush_after_jobs,
        storage_lock_timeout_seconds=resolved.storage_lock_timeout_seconds,
        storage_lock_poll_interval_seconds=resolved.storage_lock_poll_interval_seconds,
    )


@contextmanager
def open_storage_session(
    *,
    writable: bool,
    runtime_settings: Settings | None = None,
) -> Iterator[StorageRuntime]:
    """Open a storage session around a command or worker unit of work."""
    runtime = build_storage_runtime(runtime_settings)
    runtime.start_session(writable=writable)
    success = False
    try:
        yield runtime
        success = True
    finally:
        runtime.finish_session(writable=writable, success=success)
