"""Tests for versioned database migration behavior."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openreview_scraper import db

try:
    from openreview_scraper import settings
except Exception:  # pragma: no cover - optional in this slice
    settings = None


def _db_path_env(db_path: Path) -> dict[str, str]:
    return {
        "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
        "RESEARCH_DB_PATH": str(db_path),
    }


def _reset_settings_cache() -> None:
    if settings is not None and hasattr(settings, "reset_settings_cache"):
        settings.reset_settings_cache()


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_settings_cache()

    def tearDown(self) -> None:
        _reset_settings_cache()

    def test_fresh_db_applies_initial_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                applied = db.migrate()

            self.assertEqual(
                applied,
                [
                    "0001_initial.sql",
                    "0002_pdf_metadata.sql",
                    "0003_scale_indexes.sql",
                    "0004_worker_sync_jobs.sql",
                    "0005_download_jobs.sql",
                    "0006_forum_cache.sql",
                ],
            )
            with closing(sqlite3.connect(db_path)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("papers", tables)
                self.assertIn("tags", tables)
                self.assertIn("paper_tags", tables)
                self.assertIn("notes", tables)
                self.assertIn("schema_migrations", tables)
                self.assertIn("authors", tables)
                self.assertIn("paper_authors", tables)
                self.assertIn("keyword_terms", tables)
                self.assertIn("paper_keywords", tables)
                self.assertIn("sync_jobs", tables)
                self.assertIn("download_jobs", tables)
                self.assertIn("paper_forum_cache", tables)
                self.assertIn("paper_reviews", tables)
                self.assertIn("discussion_posts", tables)
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(papers)").fetchall()
                }
                self.assertIn("pdf_sha256", columns)
                self.assertIn("pdf_size_bytes", columns)

    def test_migrations_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "idempotent.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                first = db.migrate()
                second = db.migrate()

            self.assertEqual(
                first,
                [
                    "0001_initial.sql",
                    "0002_pdf_metadata.sql",
                    "0003_scale_indexes.sql",
                    "0004_worker_sync_jobs.sql",
                    "0005_download_jobs.sql",
                    "0006_forum_cache.sql",
                ],
            )
            self.assertEqual(second, [])

    def test_normalized_relation_backfill_runs_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "backfill-once.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                with patch(
                    "openreview_scraper.db._backfill_normalized_relations",
                    wraps=db._backfill_normalized_relations,
                ) as backfill:
                    first = db.migrate()
                    second = db.migrate()

            self.assertEqual(
                first,
                [
                    "0001_initial.sql",
                    "0002_pdf_metadata.sql",
                    "0003_scale_indexes.sql",
                    "0004_worker_sync_jobs.sql",
                    "0005_download_jobs.sql",
                    "0006_forum_cache.sql",
                ],
            )
            self.assertEqual(second, [])
            self.assertEqual(backfill.call_count, 1)

    def test_existing_db_upgrades_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE papers (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        authors TEXT,
                        abstract TEXT,
                        venue TEXT,
                        venueid TEXT,
                        primary_area TEXT,
                        keywords TEXT,
                        pdf_path TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO papers (id, title, authors, keywords)
                    VALUES (
                        'legacy-paper',
                        'Legacy Title',
                        '["Legacy Author"]',
                        '["legacy-keyword"]'
                    );
                    """
                )

            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                applied = db.migrate()

            self.assertEqual(
                applied,
                [
                    "0001_initial.sql",
                    "0002_pdf_metadata.sql",
                    "0003_scale_indexes.sql",
                    "0004_worker_sync_jobs.sql",
                    "0005_download_jobs.sql",
                    "0006_forum_cache.sql",
                ],
            )
            with closing(sqlite3.connect(db_path)) as conn:
                paper_count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
                migration_rows = conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations "
                    "WHERE version IN ("
                    "'0001_initial.sql', '0002_pdf_metadata.sql', "
                    "'0003_scale_indexes.sql', '0004_worker_sync_jobs.sql', "
                    "'0005_download_jobs.sql', '0006_forum_cache.sql'"
                    ")"
                ).fetchone()[0]
                author_links = conn.execute(
                    "SELECT COUNT(*) FROM paper_authors WHERE paper_id = 'legacy-paper'"
                ).fetchone()[0]
                keyword_links = conn.execute(
                    "SELECT COUNT(*) FROM paper_keywords WHERE paper_id = 'legacy-paper'"
                ).fetchone()[0]
            self.assertEqual(paper_count, 1)
            self.assertEqual(migration_rows, 6)
            self.assertEqual(author_links, 1)
            self.assertEqual(keyword_links, 1)

    def test_importing_db_module_does_not_create_or_mutate_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "import-only.db"
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "src")
            env.update(_db_path_env(db_path))

            subprocess.run(
                [sys.executable, "-c", "import openreview_scraper.db"],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertFalse(db_path.exists(), "import should not create DB file")


if __name__ == "__main__":
    unittest.main()
