"""Tests for storage runtime selection and locator mapping."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openreview_scraper import settings, storage


class StorageRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.reset_settings_cache()

    def tearDown(self) -> None:
        settings.reset_settings_cache()

    def test_local_runtime_uses_existing_local_paths(self) -> None:
        cfg = settings.load_settings(
            env={
                "OPENREVIEW_SCRAPER_DB_PATH": "/tmp/openreview/local.db",
                "OPENREVIEW_SCRAPER_PAPERS_DIR": "/tmp/openreview/papers",
            }
        )

        runtime = storage.build_storage_runtime(cfg)

        self.assertIsInstance(runtime, storage.LocalStorageRuntime)
        self.assertEqual(runtime.storage_mode, "local")
        self.assertEqual(runtime.local_db_path, Path("/tmp/openreview/local.db"))
        self.assertEqual(runtime.local_papers_dir, Path("/tmp/openreview/papers"))
        self.assertEqual(
            runtime.paper_cache_path("paper-1"),
            Path("/tmp/openreview/papers/paper-1.pdf"),
        )
        self.assertEqual(
            runtime.paper_locator("paper-1"),
            "/tmp/openreview/papers/paper-1.pdf",
        )
        self.assertEqual(
            runtime.cache_path_for_locator("/tmp/openreview/papers/paper-1.pdf"),
            Path("/tmp/openreview/papers/paper-1.pdf"),
        )
        self.assertFalse(runtime.locator_uses_remote_storage("/tmp/openreview/papers/paper-1.pdf"))

        with storage.open_storage_session(writable=False, runtime_settings=cfg) as session:
            self.assertEqual(session.paper_locator("paper-1"), runtime.paper_locator("paper-1"))

    def test_gcs_runtime_maps_remote_layout_into_local_cache_paths(self) -> None:
        cfg = settings.load_settings(
            env={
                "OPENREVIEW_SCRAPER_STORAGE_MODE": "gcs-sync",
                "OPENREVIEW_SCRAPER_GCS_BUCKET": "openreview-scraper-data",
                "OPENREVIEW_SCRAPER_GCS_PREFIX": "prod/main",
                "OPENREVIEW_SCRAPER_GCS_CACHE_DIR": "/tmp/openreview-gcs-cache",
            }
        )

        runtime = storage.build_storage_runtime(cfg)

        self.assertIsInstance(runtime, storage.GcsSyncStorageRuntime)
        self.assertEqual(runtime.storage_mode, "gcs-sync")
        self.assertEqual(runtime.local_db_path, Path("/tmp/openreview-gcs-cache/db/openreview-scraper.db"))
        self.assertEqual(runtime.local_papers_dir, Path("/tmp/openreview-gcs-cache/papers"))
        self.assertEqual(
            runtime.remote_db_uri,
            "gs://openreview-scraper-data/prod/main/db/openreview-scraper.db",
        )
        self.assertEqual(
            runtime.remote_papers_uri_prefix,
            "gs://openreview-scraper-data/prod/main/papers",
        )
        self.assertEqual(
            runtime.remote_artifacts_uri_prefix,
            "gs://openreview-scraper-data/prod/main/artifacts",
        )
        self.assertEqual(
            runtime.writer_lock_uri,
            "gs://openreview-scraper-data/prod/main/locks/sqlite-writer.json",
        )
        self.assertEqual(
            runtime.paper_locator("paper-2"),
            "gs://openreview-scraper-data/prod/main/papers/paper-2.pdf",
        )
        self.assertEqual(
            runtime.cache_path_for_locator(
                "gs://openreview-scraper-data/prod/main/papers/paper-2.pdf"
            ),
            Path("/tmp/openreview-gcs-cache/papers/paper-2.pdf"),
        )
        self.assertEqual(
            runtime.cache_path_for_locator(
                "gs://openreview-scraper-data/prod/main/db/openreview-scraper.db"
            ),
            Path("/tmp/openreview-gcs-cache/db/openreview-scraper.db"),
        )
        self.assertEqual(
            runtime.cache_path_for_locator(
                "gs://openreview-scraper-data/prod/main/artifacts/supplementary/paper-2.zip"
            ),
            Path("/tmp/openreview-gcs-cache/artifacts/supplementary/paper-2.zip"),
        )
        self.assertTrue(
            runtime.locator_uses_remote_storage(
                "gs://openreview-scraper-data/prod/main/papers/paper-2.pdf"
            )
        )
        self.assertIsNone(
            runtime.cache_path_for_locator(
                "gs://another-bucket/prod/main/papers/paper-2.pdf"
            )
        )

        with storage.open_storage_session(writable=True, runtime_settings=cfg) as session:
            self.assertEqual(session.writer_lock_uri, runtime.writer_lock_uri)


if __name__ == "__main__":
    unittest.main()
