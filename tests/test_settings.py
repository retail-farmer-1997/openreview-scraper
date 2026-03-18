"""Tests for runtime settings loading and validation."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openreview_scraper import settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.reset_settings_cache()

    def tearDown(self) -> None:
        settings.reset_settings_cache()

    @contextmanager
    def _working_directory(self, path: Path):
        previous = Path.cwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(previous)

    @staticmethod
    def _expected_default_data_dir(env: dict[str, str] | None = None) -> Path:
        resolved_env = {} if env is None else env
        if sys.platform == "darwin":
            base_dir = Path.home() / "Library" / "Application Support"
        elif os.name == "nt":
            appdata = resolved_env.get("APPDATA", "").strip()
            base_dir = Path(appdata).expanduser() if appdata else (Path.home() / "AppData" / "Roaming")
        else:
            xdg_data_home = resolved_env.get("XDG_DATA_HOME", "").strip()
            base_dir = (
                Path(xdg_data_home).expanduser()
                if xdg_data_home
                else (Path.home() / ".local" / "share")
            )
        return (base_dir / settings.DEFAULT_DATA_DIR_NAME).resolve()

    def test_defaults_use_platform_data_directory(self) -> None:
        cfg = settings.load_settings(env={})
        expected_data_dir = self._expected_default_data_dir({})

        self.assertEqual(cfg.db_path, (expected_data_dir / "openreview-scraper.db").resolve())
        self.assertEqual(cfg.papers_dir, (expected_data_dir / "papers").resolve())
        self.assertEqual(cfg.storage_mode, "local")
        self.assertIsNone(cfg.gcs_bucket)
        self.assertEqual(cfg.gcs_prefix, "")
        self.assertEqual(cfg.gcs_cache_dir, (expected_data_dir / "gcs-cache").resolve())
        self.assertEqual(cfg.storage_sync_interval_seconds, 60.0)
        self.assertEqual(cfg.storage_flush_after_jobs, 25)
        self.assertEqual(cfg.storage_lock_timeout_seconds, 300.0)
        self.assertEqual(cfg.storage_lock_poll_interval_seconds, 5.0)
        self.assertEqual(cfg.openreview_api_url, "https://api2.openreview.net")
        self.assertEqual(cfg.openreview_web_url, "https://openreview.net")
        self.assertIsNone(cfg.openreview_username)
        self.assertIsNone(cfg.openreview_password)
        self.assertIsNone(cfg.openreview_token)
        self.assertEqual(cfg.http_timeout_seconds, 30.0)
        self.assertEqual(cfg.http_max_retries, 0)
        self.assertEqual(cfg.http_retry_backoff_seconds, 1.0)
        self.assertEqual(cfg.http_retry_jitter_seconds, 0.1)
        self.assertEqual(cfg.openreview_min_request_interval_seconds, 12.0)
        self.assertEqual(cfg.openreview_rate_limit_buffer_seconds, 2.0)
        self.assertEqual(cfg.db_busy_timeout_ms, 5000)
        self.assertEqual(cfg.download_job_lease_seconds, 900)

    @unittest.skipIf(
        sys.platform == "darwin" or os.name == "nt",
        "XDG applies to Unix-like systems",
    )
    def test_xdg_data_home_updates_default_paths(self) -> None:
        cfg = settings.load_settings(env={"XDG_DATA_HOME": "/tmp/research-xdg"})

        self.assertEqual(cfg.db_path, Path("/tmp/research-xdg/openreview_scraper/openreview-scraper.db").resolve())
        self.assertEqual(cfg.papers_dir, Path("/tmp/research-xdg/openreview_scraper/papers").resolve())

    def test_env_overrides_preferred_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir)
            with self._working_directory(runtime_root):
                cfg = settings.load_settings(
                    env={
                        "OPENREVIEW_SCRAPER_DB_PATH": "tmp/custom-repo.db",
                        "OPENREVIEW_SCRAPER_PAPERS_DIR": "/tmp/scraper-papers",
                        "OPENREVIEW_SCRAPER_OPENREVIEW_API_URL": "https://example.org/openreview-api",
                        "OPENREVIEW_SCRAPER_OPENREVIEW_WEB_URL": "https://example.org",
                        "OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME": "alice@example.com",
                        "OPENREVIEW_SCRAPER_OPENREVIEW_PASSWORD": "secret",
                        "OPENREVIEW_SCRAPER_OPENREVIEW_TOKEN": "token-123",
                        "OPENREVIEW_SCRAPER_HTTP_TIMEOUT_SECONDS": "12.5",
                        "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "3",
                        "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS": "0.25",
                        "OPENREVIEW_SCRAPER_HTTP_RETRY_JITTER_SECONDS": "0.05",
                        "OPENREVIEW_SCRAPER_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS": "4.5",
                        "OPENREVIEW_SCRAPER_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS": "1.25",
                        "OPENREVIEW_SCRAPER_DB_BUSY_TIMEOUT_MS": "7500",
                        "OPENREVIEW_SCRAPER_DOWNLOAD_JOB_LEASE_SECONDS": "1800",
                    }
                )

        self.assertEqual(cfg.db_path, (runtime_root / "tmp" / "custom-repo.db").resolve())
        self.assertEqual(cfg.papers_dir, Path("/tmp/scraper-papers").resolve())
        self.assertEqual(cfg.openreview_api_url, "https://example.org/openreview-api")
        self.assertEqual(cfg.openreview_web_url, "https://example.org")
        self.assertEqual(cfg.openreview_username, "alice@example.com")
        self.assertEqual(cfg.openreview_password, "secret")
        self.assertEqual(cfg.openreview_token, "token-123")
        self.assertEqual(cfg.http_timeout_seconds, 12.5)
        self.assertEqual(cfg.http_max_retries, 3)
        self.assertEqual(cfg.http_retry_backoff_seconds, 0.25)
        self.assertEqual(cfg.http_retry_jitter_seconds, 0.05)
        self.assertEqual(cfg.openreview_min_request_interval_seconds, 4.5)
        self.assertEqual(cfg.openreview_rate_limit_buffer_seconds, 1.25)
        self.assertEqual(cfg.db_busy_timeout_ms, 7500)
        self.assertEqual(cfg.download_job_lease_seconds, 1800)

    def test_research_legacy_env_is_used_when_clean_prefix_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir)
            with self._working_directory(runtime_root):
                cfg = settings.load_settings(
                    env={
                        "RESEARCH_DB_PATH": "tmp/research-legacy.db",
                        "RESEARCH_PAPERS_DIR": "/tmp/research-legacy-papers",
                        "RESEARCH_OPENREVIEW_API_URL": "https://legacy.example.org",
                        "RESEARCH_OPENREVIEW_WEB_URL": "https://legacy-web.example.org",
                        "RESEARCH_OPENREVIEW_USERNAME": "legacy-username",
                        "RESEARCH_OPENREVIEW_PASSWORD": "legacy-password",
                        "RESEARCH_OPENREVIEW_TOKEN": "legacy-token",
                    }
                )

        self.assertEqual(cfg.openreview_username, "legacy-username")
        self.assertEqual(cfg.openreview_password, "legacy-password")
        self.assertEqual(cfg.openreview_token, "legacy-token")
        self.assertEqual(cfg.openreview_api_url, "https://legacy.example.org")
        self.assertEqual(cfg.openreview_web_url, "https://legacy-web.example.org")
        self.assertEqual(cfg.db_path, (runtime_root / "tmp" / "research-legacy.db").resolve())
        self.assertEqual(cfg.papers_dir, Path("/tmp/research-legacy-papers").resolve())

    def test_prefers_clean_auth_over_legacy_when_both_present(self) -> None:
        cfg = settings.load_settings(
            env={
                "OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME": "new-user",
                "OPENREVIEW_SCRAPER_OPENREVIEW_PASSWORD": "new-password",
                "OPENREVIEW_SCRAPER_OPENREVIEW_TOKEN": "new-token",
                "RESEARCH_OPENREVIEW_USERNAME": "old-user",
                "RESEARCH_OPENREVIEW_PASSWORD": "old-password",
                "RESEARCH_OPENREVIEW_TOKEN": "old-token",
            }
        )

        self.assertEqual(cfg.openreview_username, "new-user")
        self.assertEqual(cfg.openreview_password, "new-password")
        self.assertEqual(cfg.openreview_token, "new-token")

    def test_invalid_url_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENREVIEW_SCRAPER_OPENREVIEW_API_URL"):
            settings.load_settings(env={"OPENREVIEW_SCRAPER_OPENREVIEW_API_URL": "not-a-url"})

    def test_invalid_timeout_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENREVIEW_SCRAPER_HTTP_TIMEOUT_SECONDS"):
            settings.load_settings(env={"OPENREVIEW_SCRAPER_HTTP_TIMEOUT_SECONDS": "0"})

    def test_gcs_sync_mode_normalizes_bucket_prefix_and_cache_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir)
            with self._working_directory(runtime_root):
                cfg = settings.load_settings(
                    env={
                        "OPENREVIEW_SCRAPER_STORAGE_MODE": "gcs-sync",
                        "OPENREVIEW_SCRAPER_GCS_BUCKET": "gs://openreview-scraper-data",
                        "OPENREVIEW_SCRAPER_GCS_PREFIX": "/team//daily-sync/",
                        "OPENREVIEW_SCRAPER_GCS_CACHE_DIR": "tmp/gcs-cache",
                        "OPENREVIEW_SCRAPER_STORAGE_SYNC_INTERVAL_SECONDS": "120",
                        "OPENREVIEW_SCRAPER_STORAGE_FLUSH_AFTER_JOBS": "7",
                        "OPENREVIEW_SCRAPER_STORAGE_LOCK_TIMEOUT_SECONDS": "42.5",
                        "OPENREVIEW_SCRAPER_STORAGE_LOCK_POLL_INTERVAL_SECONDS": "0.5",
                    }
                )

        self.assertEqual(cfg.storage_mode, "gcs-sync")
        self.assertEqual(cfg.gcs_bucket, "openreview-scraper-data")
        self.assertEqual(cfg.gcs_prefix, "team/daily-sync")
        self.assertEqual(cfg.gcs_cache_dir, (runtime_root / "tmp" / "gcs-cache").resolve())
        self.assertEqual(cfg.storage_sync_interval_seconds, 120.0)
        self.assertEqual(cfg.storage_flush_after_jobs, 7)
        self.assertEqual(cfg.storage_lock_timeout_seconds, 42.5)
        self.assertEqual(cfg.storage_lock_poll_interval_seconds, 0.5)

    def test_relative_paths_resolve_from_current_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir)
            with self._working_directory(runtime_root):
                cfg = settings.load_settings(
                    env={
                        "OPENREVIEW_SCRAPER_DB_PATH": "state/app.db",
                        "OPENREVIEW_SCRAPER_PAPERS_DIR": "papers/cache",
                        "OPENREVIEW_SCRAPER_STORAGE_MODE": "gcs-sync",
                        "OPENREVIEW_SCRAPER_GCS_BUCKET": "openreview-scraper-data",
                        "OPENREVIEW_SCRAPER_GCS_CACHE_DIR": "gcs/cache",
                    }
                )

        self.assertEqual(cfg.db_path, (runtime_root / "state" / "app.db").resolve())
        self.assertEqual(cfg.papers_dir, (runtime_root / "papers" / "cache").resolve())
        self.assertEqual(cfg.gcs_cache_dir, (runtime_root / "gcs" / "cache").resolve())

    def test_gcs_sync_mode_requires_bucket(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENREVIEW_SCRAPER_GCS_BUCKET is required"):
            settings.load_settings(env={"OPENREVIEW_SCRAPER_STORAGE_MODE": "gcs-sync"})

    def test_invalid_storage_mode_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENREVIEW_SCRAPER_STORAGE_MODE"):
            settings.load_settings(env={"OPENREVIEW_SCRAPER_STORAGE_MODE": "s3"})

    def test_local_mode_ignores_unused_gcs_settings(self) -> None:
        cfg = settings.load_settings(
            env={
                "OPENREVIEW_SCRAPER_STORAGE_MODE": "local",
                "OPENREVIEW_SCRAPER_GCS_BUCKET": "not/a/bucket",
                "OPENREVIEW_SCRAPER_GCS_PREFIX": "gs://wrong-shape",
            }
        )

        self.assertEqual(cfg.storage_mode, "local")
        self.assertIsNone(cfg.gcs_bucket)
        self.assertEqual(cfg.gcs_prefix, "")

    def test_empty_env_value_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENREVIEW_SCRAPER_DB_PATH cannot be empty"):
            settings.load_settings(env={"OPENREVIEW_SCRAPER_DB_PATH": "   "})

    def test_get_settings_reads_process_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENREVIEW_SCRAPER_OPENREVIEW_WEB_URL": "https://override.example"},
            clear=False,
        ):
            settings.reset_settings_cache()
            cfg = settings.get_settings()

        self.assertEqual(cfg.openreview_web_url, "https://override.example")

    def test_openreview_auth_falls_back_to_library_env_names(self) -> None:
        cfg = settings.load_settings(
            env={
                "OPENREVIEW_USERNAME": "legacy@example.com",
                "OPENREVIEW_PASSWORD": "legacy-secret",
                "OPENREVIEW_TOKEN": "legacy-token",
            }
        )

        self.assertEqual(cfg.openreview_username, "legacy@example.com")
        self.assertEqual(cfg.openreview_password, "legacy-secret")
        self.assertEqual(cfg.openreview_token, "legacy-token")


if __name__ == "__main__":
    unittest.main()
