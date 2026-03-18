"""Runtime settings and environment overrides."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Mapping
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR_NAME = "openreview_scraper"
DEFAULT_GCS_CACHE_DIR_NAME = "gcs-cache"
DEFAULT_OPENREVIEW_API_URL = "https://api2.openreview.net"
DEFAULT_OPENREVIEW_WEB_URL = "https://openreview.net"
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
DEFAULT_HTTP_MAX_RETRIES = 0
DEFAULT_HTTP_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_HTTP_RETRY_JITTER_SECONDS = 0.1
DEFAULT_STORAGE_MODE = "local"
STORAGE_MODES = ("local", "gcs-sync")
DEFAULT_STORAGE_SYNC_INTERVAL_SECONDS = 60.0
DEFAULT_STORAGE_FLUSH_AFTER_JOBS = 25
DEFAULT_STORAGE_LOCK_TIMEOUT_SECONDS = 300.0
DEFAULT_STORAGE_LOCK_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS = 12.0
DEFAULT_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS = 2.0
DEFAULT_DB_BUSY_TIMEOUT_MS = 5000
DEFAULT_DOWNLOAD_JOB_LEASE_SECONDS = 900


@dataclass(frozen=True)
class Settings:
    """Runtime settings used by the application."""

    db_path: Path
    papers_dir: Path
    storage_mode: str
    gcs_bucket: str | None
    gcs_prefix: str
    gcs_cache_dir: Path
    storage_sync_interval_seconds: float
    storage_flush_after_jobs: int
    storage_lock_timeout_seconds: float
    storage_lock_poll_interval_seconds: float
    openreview_api_url: str
    openreview_web_url: str
    openreview_username: str | None
    openreview_password: str | None
    openreview_token: str | None
    http_timeout_seconds: float
    http_max_retries: int
    http_retry_backoff_seconds: float
    http_retry_jitter_seconds: float
    openreview_min_request_interval_seconds: float
    openreview_rate_limit_buffer_seconds: float
    db_busy_timeout_ms: int
    download_job_lease_seconds: int


def _default_data_dir(env: Mapping[str, str]) -> Path:
    if sys.platform == "darwin":
        base_dir = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        appdata = env.get("APPDATA", "").strip()
        base_dir = Path(appdata).expanduser() if appdata else (Path.home() / "AppData" / "Roaming")
    else:
        xdg_data_home = env.get("XDG_DATA_HOME", "").strip()
        if xdg_data_home:
            base_dir = Path(xdg_data_home).expanduser()
        else:
            base_dir = Path.home() / ".local" / "share"
    return (base_dir / DEFAULT_DATA_DIR_NAME).resolve()


def _relative_path_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return PROJECT_ROOT


def _read_env(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        raise ValueError(f"{key} cannot be empty")
    return value


def _first_present_env(env: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        value = _read_env(env, key)
        if value is not None:
            return value
    return None


def _path_setting(env: Mapping[str, str], *keys: str, default: Path) -> Path:
    raw = _first_present_env(env, *keys)
    if raw is None:
        return default.resolve()

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = _relative_path_root() / candidate
    return candidate.resolve()


def _storage_mode_setting(env: Mapping[str, str]) -> str:
    raw = _read_env(env, "OPENREVIEW_SCRAPER_STORAGE_MODE")
    if raw is None:
        return DEFAULT_STORAGE_MODE

    normalized = raw.lower()
    if normalized not in STORAGE_MODES:
        raise ValueError(
            "OPENREVIEW_SCRAPER_STORAGE_MODE must be one of "
            f"{STORAGE_MODES}, got: {raw!r}"
        )
    return normalized


def _url_setting(env: Mapping[str, str], *keys: str, default: str) -> str:
    raw = _first_present_env(env, *keys)
    source_key = next((key for key in keys if env.get(key) is not None), None)
    value = default if raw is None else raw
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        source_key = "default" if source_key is None else source_key
        raise ValueError(f"{source_key} must be an absolute http(s) URL, got: {value!r}")
    return value.rstrip("/")


def _float_setting(
    env: Mapping[str, str], *keys: str, default: float, min_value: float
) -> float:
    raw = _first_present_env(env, *keys)
    source_key = next((key for key in keys if env.get(key) is not None), None)
    if raw is None:
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        source_key = "default" if source_key is None else source_key
        raise ValueError(f"{source_key} must be a number, got: {raw!r}") from exc

    if value < min_value:
        source_key = "default" if source_key is None else source_key
        raise ValueError(f"{source_key} must be >= {min_value}, got: {value}")
    return value


def _int_setting(
    env: Mapping[str, str], *keys: str, default: int, min_value: int
) -> int:
    raw = _first_present_env(env, *keys)
    source_key = next((key for key in keys if env.get(key) is not None), None)
    if raw is None:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        source_key = "default" if source_key is None else source_key
        raise ValueError(f"{source_key} must be an integer, got: {raw!r}") from exc

    if value < min_value:
        source_key = "default" if source_key is None else source_key
        raise ValueError(f"{source_key} must be >= {min_value}, got: {value}")
    return value


def _gcs_bucket_setting(env: Mapping[str, str]) -> str:
    source_key = "OPENREVIEW_SCRAPER_GCS_BUCKET"
    raw = _read_env(env, source_key)
    if raw is None:
        raise ValueError(f"{source_key} is required when OPENREVIEW_SCRAPER_STORAGE_MODE='gcs-sync'")

    bucket = raw
    if raw.startswith("gs://"):
        parsed = urlparse(raw)
        if parsed.scheme != "gs" or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError(
                f"{source_key} must be a bare bucket name or gs://bucket URI, got: {raw!r}"
            )
        bucket = parsed.netloc
    elif "/" in raw:
        raise ValueError(
            f"{source_key} must be a bare bucket name or gs://bucket URI, got: {raw!r}"
        )

    normalized = bucket.strip("/")
    if not normalized:
        raise ValueError(f"{source_key} cannot be empty")
    return normalized


def _gcs_prefix_setting(env: Mapping[str, str]) -> str:
    raw = _read_env(env, "OPENREVIEW_SCRAPER_GCS_PREFIX")
    if raw is None:
        return ""
    if raw.startswith("gs://"):
        raise ValueError(
            "OPENREVIEW_SCRAPER_GCS_PREFIX must be a bucket-relative prefix, got a gs:// URI"
        )
    return "/".join(segment for segment in raw.split("/") if segment)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from environment with defaults and validation."""
    resolved_env = os.environ if env is None else env
    default_data_dir = _default_data_dir(resolved_env)
    storage_mode = _storage_mode_setting(resolved_env)

    if storage_mode == "gcs-sync":
        gcs_bucket = _gcs_bucket_setting(resolved_env)
        gcs_prefix = _gcs_prefix_setting(resolved_env)
        gcs_cache_dir = _path_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_GCS_CACHE_DIR",
            default=default_data_dir / DEFAULT_GCS_CACHE_DIR_NAME,
        )
        storage_sync_interval_seconds = _float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_STORAGE_SYNC_INTERVAL_SECONDS",
            default=DEFAULT_STORAGE_SYNC_INTERVAL_SECONDS,
            min_value=0.0,
        )
        storage_flush_after_jobs = _int_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_STORAGE_FLUSH_AFTER_JOBS",
            default=DEFAULT_STORAGE_FLUSH_AFTER_JOBS,
            min_value=0,
        )
        storage_lock_timeout_seconds = _float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_STORAGE_LOCK_TIMEOUT_SECONDS",
            default=DEFAULT_STORAGE_LOCK_TIMEOUT_SECONDS,
            min_value=0.001,
        )
        storage_lock_poll_interval_seconds = _float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_STORAGE_LOCK_POLL_INTERVAL_SECONDS",
            default=DEFAULT_STORAGE_LOCK_POLL_INTERVAL_SECONDS,
            min_value=0.001,
        )
    else:
        gcs_bucket = None
        gcs_prefix = ""
        gcs_cache_dir = (default_data_dir / DEFAULT_GCS_CACHE_DIR_NAME).resolve()
        storage_sync_interval_seconds = DEFAULT_STORAGE_SYNC_INTERVAL_SECONDS
        storage_flush_after_jobs = DEFAULT_STORAGE_FLUSH_AFTER_JOBS
        storage_lock_timeout_seconds = DEFAULT_STORAGE_LOCK_TIMEOUT_SECONDS
        storage_lock_poll_interval_seconds = DEFAULT_STORAGE_LOCK_POLL_INTERVAL_SECONDS

    return Settings(
        db_path=_path_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_DB_PATH",
            "RESEARCH_DB_PATH",
            default=default_data_dir / "openreview-scraper.db",
        ),
        papers_dir=_path_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_PAPERS_DIR",
            "RESEARCH_PAPERS_DIR",
            default=default_data_dir / "papers",
        ),
        storage_mode=storage_mode,
        gcs_bucket=gcs_bucket,
        gcs_prefix=gcs_prefix,
        gcs_cache_dir=gcs_cache_dir,
        storage_sync_interval_seconds=storage_sync_interval_seconds,
        storage_flush_after_jobs=storage_flush_after_jobs,
        storage_lock_timeout_seconds=storage_lock_timeout_seconds,
        storage_lock_poll_interval_seconds=storage_lock_poll_interval_seconds,
        openreview_api_url=_url_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_OPENREVIEW_API_URL",
            "RESEARCH_OPENREVIEW_API_URL",
            default=DEFAULT_OPENREVIEW_API_URL,
        ),
        openreview_web_url=_url_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_OPENREVIEW_WEB_URL",
            "RESEARCH_OPENREVIEW_WEB_URL",
            default=DEFAULT_OPENREVIEW_WEB_URL,
        ),
        openreview_username=_first_present_env(
            resolved_env,
            "OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME",
            "RESEARCH_OPENREVIEW_USERNAME",
            "OPENREVIEW_USERNAME",
        ),
        openreview_password=_first_present_env(
            resolved_env,
            "OPENREVIEW_SCRAPER_OPENREVIEW_PASSWORD",
            "RESEARCH_OPENREVIEW_PASSWORD",
            "OPENREVIEW_PASSWORD",
        ),
        openreview_token=_first_present_env(
            resolved_env,
            "OPENREVIEW_SCRAPER_OPENREVIEW_TOKEN",
            "RESEARCH_OPENREVIEW_TOKEN",
            "OPENREVIEW_TOKEN",
        ),
        http_timeout_seconds=_float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_HTTP_TIMEOUT_SECONDS",
            "RESEARCH_HTTP_TIMEOUT_SECONDS",
            default=DEFAULT_HTTP_TIMEOUT_SECONDS,
            min_value=0.001
        ),
        http_max_retries=_int_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES",
            "RESEARCH_HTTP_MAX_RETRIES",
            default=DEFAULT_HTTP_MAX_RETRIES,
            min_value=0,
        ),
        http_retry_backoff_seconds=_float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS",
            "RESEARCH_HTTP_RETRY_BACKOFF_SECONDS",
            default=DEFAULT_HTTP_RETRY_BACKOFF_SECONDS,
            min_value=0.0
        ),
        http_retry_jitter_seconds=_float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_HTTP_RETRY_JITTER_SECONDS",
            "RESEARCH_HTTP_RETRY_JITTER_SECONDS",
            default=DEFAULT_HTTP_RETRY_JITTER_SECONDS,
            min_value=0.0
        ),
        openreview_min_request_interval_seconds=_float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS",
            "RESEARCH_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS",
            default=DEFAULT_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS,
            min_value=0.0,
        ),
        openreview_rate_limit_buffer_seconds=_float_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS",
            "RESEARCH_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS",
            default=DEFAULT_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS,
            min_value=0.0,
        ),
        db_busy_timeout_ms=_int_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_DB_BUSY_TIMEOUT_MS",
            "RESEARCH_DB_BUSY_TIMEOUT_MS",
            default=DEFAULT_DB_BUSY_TIMEOUT_MS,
            min_value=1,
        ),
        download_job_lease_seconds=_int_setting(
            resolved_env,
            "OPENREVIEW_SCRAPER_DOWNLOAD_JOB_LEASE_SECONDS",
            "RESEARCH_DOWNLOAD_JOB_LEASE_SECONDS",
            default=DEFAULT_DOWNLOAD_JOB_LEASE_SECONDS,
            min_value=1,
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get memoized settings for application runtime."""
    return load_settings()


def reset_settings_cache() -> None:
    """Clear cached settings (primarily for tests)."""
    get_settings.cache_clear()
