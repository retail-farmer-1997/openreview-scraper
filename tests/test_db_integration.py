"""Integration tests for DB CRUD behavior with temporary databases."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
import sys

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


class DBIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_settings_cache()

    def tearDown(self) -> None:
        _reset_settings_cache()

    def test_insert_and_get_paper_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "crud.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()

                inserted = db.insert_paper(
                    paper_id="paper-1",
                    title="A Test Paper",
                    authors=["A. Author", "B. Author"],
                    abstract="Abstract text",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                    primary_area="Optimization",
                    keywords=["diffusion"],
                )
                duplicate_insert = db.insert_paper(
                    paper_id="paper-1",
                    title="A Test Paper",
                    authors=["A. Author", "B. Author"],
                    abstract="Abstract text",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                    primary_area="Optimization",
                    keywords=["diffusion"],
                )
                paper = db.get_paper("paper-1")

            self.assertTrue(inserted)
            self.assertFalse(duplicate_insert)
            self.assertIsNotNone(paper)
            self.assertEqual(paper["id"], "paper-1")
            self.assertEqual(paper["authors"], ["A. Author", "B. Author"])
            self.assertEqual(paper["keywords"], ["diffusion"])

    def test_tags_notes_and_download_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "filters.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()

                db.insert_paper(
                    paper_id="paper-1",
                    title="Downloaded Paper",
                    authors=["Alice"],
                    abstract="A",
                    venue="NeurIPS 2025 oral",
                    venueid="NeurIPS/2025",
                    keywords=["diffusion"],
                )
                db.insert_paper(
                    paper_id="paper-2",
                    title="Metadata Only Paper",
                    authors=["Bob"],
                    abstract="B",
                    venue="NeurIPS 2025 oral",
                    venueid="NeurIPS/2025",
                    keywords=["alignment"],
                )

                db.update_pdf_path("paper-1", "/tmp/paper-1.pdf")
                db.add_tag("paper-1", "diffusion")
                db.add_note("paper-1", "Interesting method.")

                downloaded = db.list_papers(downloaded_only=True)
                tagged = db.list_papers(tag="diffusion")
                by_author = db.list_papers(author="Alice")
                by_keyword = db.list_papers(keyword="align")
                notes = db.get_paper_notes("paper-1")
                tags = db.get_paper_tags("paper-1")

            self.assertEqual(len(downloaded), 1)
            self.assertEqual(downloaded[0]["id"], "paper-1")
            self.assertEqual(tagged[0]["id"], "paper-1")
            self.assertEqual(len(by_author), 1)
            self.assertEqual(by_author[0]["id"], "paper-1")
            self.assertEqual(len(by_keyword), 1)
            self.assertEqual(by_keyword[0]["id"], "paper-2")
            self.assertEqual(tags, ["diffusion"])
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["content"], "Interesting method.")

    def test_upsert_paper_reports_created_updated_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "upsert.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()

                created = db.upsert_paper(
                    paper_id="paper-1",
                    title="Title A",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                    keywords=["diffusion"],
                )
                skipped = db.upsert_paper(
                    paper_id="paper-1",
                    title="Title A",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                    keywords=["diffusion"],
                )
                updated = db.upsert_paper(
                    paper_id="paper-1",
                    title="Title B",
                    authors=["Alice", "Bob"],
                    abstract="B",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                    keywords=["diffusion", "rl"],
                )

            self.assertEqual(created, "created")
            self.assertEqual(skipped, "skipped")
            self.assertEqual(updated, "updated")

    def test_update_pdf_metadata_persists_checksum_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "pdf-metadata.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="paper-1",
                    title="Title A",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )
                db.update_pdf_metadata(
                    paper_id="paper-1",
                    pdf_path="/tmp/paper-1.pdf",
                    pdf_sha256="abc123",
                    pdf_size_bytes=42,
                )
                paper = db.get_paper("paper-1")

            self.assertEqual(paper["pdf_path"], "/tmp/paper-1.pdf")
            self.assertEqual(paper["pdf_sha256"], "abc123")
            self.assertEqual(paper["pdf_size_bytes"], 42)

    def test_forum_cache_roundtrip_replaces_reviews_and_posts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "forum-cache.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="paper-1",
                    title="Forum Cache Paper",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )

                db.replace_paper_forum_cache(
                    paper_id="paper-1",
                    reviews=[
                        {
                            "id": "review-1",
                            "reviewer": "Reviewer 1",
                            "rating": "8: Strong Accept",
                            "confidence": "4: High",
                            "summary": "Strong work",
                            "strengths": "Clear framing",
                            "weaknesses": "Minor ablations",
                            "questions": "None",
                            "limitations": "Limited scale",
                            "soundness": "Strong",
                            "presentation": "Clear",
                            "contribution": "High",
                            "recommendation": "Accept",
                            "full_text": "Structured review body",
                            "created_at_ms": 1710000000000,
                        }
                    ],
                    posts=[
                        {
                            "id": "review-1",
                            "reply_to": None,
                            "author": "Reviewer 1",
                            "content": "Structured review body",
                            "post_type": "review",
                            "title": None,
                            "created_at_ms": 1710000000000,
                        },
                        {
                            "id": "comment-1",
                            "reply_to": "review-1",
                            "author": "Authors",
                            "content": "Thanks for the feedback",
                            "post_type": "rebuttal",
                            "title": "Author Response",
                            "created_at_ms": 1710003600000,
                        },
                    ],
                )

                cache = db.get_paper_forum_cache("paper-1")
                reviews = db.get_cached_paper_reviews("paper-1")
                posts = db.get_cached_discussion_posts("paper-1")

                db.replace_paper_forum_cache(
                    paper_id="paper-1",
                    reviews=[],
                    posts=[],
                )
                refreshed_cache = db.get_paper_forum_cache("paper-1")
                refreshed_reviews = db.get_cached_paper_reviews("paper-1")
                refreshed_posts = db.get_cached_discussion_posts("paper-1")

            self.assertEqual(cache["review_count"], 1)
            self.assertEqual(cache["post_count"], 2)
            self.assertEqual(reviews[0]["reviewer"], "Reviewer 1")
            self.assertEqual(posts[1]["post_type"], "rebuttal")
            self.assertIsNotNone(datetime.fromtimestamp(reviews[0]["created_at_ms"] / 1000))
            self.assertEqual(refreshed_cache["review_count"], 0)
            self.assertEqual(refreshed_cache["post_count"], 0)
            self.assertEqual(refreshed_reviews, [])
            self.assertEqual(refreshed_posts, [])

    def test_search_downloaded_papers_returns_matches_and_file_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "search-downloaded.db"
            available_pdf = Path(tmpdir) / "alpha.pdf"
            missing_pdf = Path(tmpdir) / "missing.pdf"
            available_pdf.write_bytes(b"%PDF-1.4 alpha")

            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="alpha-paper",
                    title="Alpha Insights",
                    authors=["Alice Example"],
                    abstract="A",
                    venue="ICLR 2026",
                    venueid="ICLR/2026",
                    keywords=["alpha"],
                )
                db.upsert_paper(
                    paper_id="beta-paper",
                    title="Beta Notes",
                    authors=["Bob Example"],
                    abstract="B",
                    venue="NeurIPS 2026",
                    venueid="NeurIPS/2026",
                    keywords=["beta"],
                )
                db.upsert_paper(
                    paper_id="gamma-paper",
                    title="Gamma Not Downloaded",
                    authors=["Carol Example"],
                    abstract="C",
                    venue="ICLR 2026",
                    venueid="ICLR/2026",
                )
                db.update_pdf_metadata(
                    paper_id="alpha-paper",
                    pdf_path=str(available_pdf),
                    pdf_sha256="sha-alpha",
                    pdf_size_bytes=len(b"%PDF-1.4 alpha"),
                )
                db.update_pdf_metadata(
                    paper_id="beta-paper",
                    pdf_path=str(missing_pdf),
                    pdf_sha256="sha-beta",
                    pdf_size_bytes=21,
                )

                alpha_matches = db.search_downloaded_papers(query="Alpha", limit=10)
                all_matches = db.search_downloaded_papers(query="", limit=10)
                downloaded = db.get_downloaded_paper_file("alpha-paper")
                missing = db.get_downloaded_paper_file("gamma-paper")

            self.assertEqual(len(alpha_matches), 1)
            self.assertEqual(alpha_matches[0]["id"], "alpha-paper")
            self.assertTrue(alpha_matches[0]["file_exists"])
            self.assertIn("Alice Example", alpha_matches[0]["authors"])
            self.assertEqual({paper["id"] for paper in all_matches}, {"alpha-paper", "beta-paper"})
            missing_entry = next(p for p in all_matches if p["id"] == "beta-paper")
            self.assertFalse(missing_entry["file_exists"])
            self.assertIsNotNone(downloaded)
            self.assertEqual(downloaded["pdf_path"], str(available_pdf))
            self.assertIsNone(missing)

    def test_search_papers_matches_tags_authors_and_queue_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "search-library.db"
            available_pdf = Path(tmpdir) / "alpha.pdf"
            available_pdf.write_bytes(b"%PDF-1.4 alpha")

            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="alpha-paper",
                    title="Alpha Systems",
                    authors=["Alice Example"],
                    abstract="A",
                    venue="ICLR 2026",
                    venueid="ICLR/2026",
                    primary_area="Systems",
                    keywords=["agents"],
                )
                db.upsert_paper(
                    paper_id="beta-paper",
                    title="Beta Retrieval",
                    authors=["Bob Example"],
                    abstract="B",
                    venue="NeurIPS 2026",
                    venueid="NeurIPS/2026",
                    primary_area="Representation Learning",
                    keywords=["retrieval"],
                )
                db.add_tag("beta-paper", "survey")
                db.update_pdf_metadata(
                    paper_id="alpha-paper",
                    pdf_path=str(available_pdf),
                    pdf_sha256="sha-alpha",
                    pdf_size_bytes=len(b"%PDF-1.4 alpha"),
                )
                db.enqueue_download_job("beta-paper")

                tag_matches = db.search_papers(query="survey", limit=10)
                author_matches = db.search_papers(query="Alice Example", limit=10)
                beta_paper = db.get_library_paper("beta-paper")

            self.assertEqual(len(tag_matches), 1)
            self.assertEqual(tag_matches[0]["id"], "beta-paper")
            self.assertIn("survey", tag_matches[0]["tags"])
            self.assertFalse(tag_matches[0]["has_pdf"])
            self.assertTrue(tag_matches[0]["missing_record"])
            self.assertEqual(tag_matches[0]["latest_download_status"], "pending")
            self.assertEqual(len(author_matches), 1)
            self.assertEqual(author_matches[0]["id"], "alpha-paper")
            self.assertTrue(author_matches[0]["pdf_ready"])
            self.assertIsNotNone(beta_paper)
            self.assertEqual(beta_paper["id"], "beta-paper")
            self.assertEqual(beta_paper["latest_download_status"], "pending")
            self.assertIn("survey", beta_paper["tags"])

    def test_connection_applies_concurrent_safe_pragmas(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "pragmas.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                with db.get_connection() as conn:
                    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

            self.assertEqual(str(journal_mode).lower(), "wal")
            self.assertEqual(int(busy_timeout), 5000)
            self.assertEqual(int(foreign_keys), 1)

    def test_download_job_queue_and_db_stats_capture_reconcile_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "download-queue.db"
            existing_pdf = Path(tmpdir) / "paper-1.pdf"
            missing_pdf = Path(tmpdir) / "paper-2.pdf"
            existing_pdf.write_bytes(b"%PDF-1.4 present")

            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="paper-1",
                    title="Downloaded",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-2",
                    title="Missing File",
                    authors=["Bob"],
                    abstract="B",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-3",
                    title="No Download",
                    authors=["Carol"],
                    abstract="C",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )
                db.update_pdf_metadata(
                    paper_id="paper-1",
                    pdf_path=str(existing_pdf),
                    pdf_sha256="sha-present",
                    pdf_size_bytes=16,
                )
                db.update_pdf_metadata(
                    paper_id="paper-2",
                    pdf_path=str(missing_pdf),
                    pdf_sha256="sha-missing",
                    pdf_size_bytes=99,
                )

                queued = db.enqueue_reconcile_download_jobs()
                stats = db.get_db_stats()
                queue_status = db.get_download_queue_status()
                reconcile_papers = db.list_papers_needing_reconcile(limit=5)

            self.assertEqual(queued["candidates"], 2)
            self.assertEqual(queued["created"], 2)
            self.assertEqual(stats["papers"]["total"], 3)
            self.assertEqual(stats["papers"]["downloaded_recorded"], 2)
            self.assertEqual(stats["papers"]["missing_record"], 1)
            self.assertEqual(stats["papers"]["needs_reconcile"], 2)
            self.assertEqual(stats["papers"]["missing_files"], 1)
            self.assertEqual(queue_status["counts"]["pending"], 2)
            self.assertEqual([paper["id"] for paper in reconcile_papers], ["paper-2", "paper-3"])
            self.assertTrue(reconcile_papers[0]["missing_file"])
            self.assertTrue(reconcile_papers[1]["missing_record"])

    def test_enqueue_reconcile_download_jobs_prioritizes_decision_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "priority-enqueue.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="paper-poster",
                    title="Poster Paper",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Poster",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-other",
                    title="Workshop Paper",
                    authors=["Bob"],
                    abstract="B",
                    venue="ICLR 2025 Workshop",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-oral",
                    title="Oral Paper",
                    authors=["Carol"],
                    abstract="C",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-spotlight",
                    title="Spotlight Paper",
                    authors=["Dora"],
                    abstract="D",
                    venue="ICLR 2025 Spotlight",
                    venueid="ICLR/2025",
                )

                queued = db.enqueue_reconcile_download_jobs(limit=3)
                queued_paper_ids = [
                    db.get_download_job(job_id)["paper_id"] for job_id in queued["job_ids"]
                ]

            self.assertEqual(queued["candidates"], 3)
            self.assertEqual(queued["created"], 3)
            self.assertEqual(queued_paper_ids, ["paper-oral", "paper-spotlight", "paper-poster"])

    def test_download_job_claim_prefers_higher_priority_over_older_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "priority-claim.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="paper-poster",
                    title="Poster Paper",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Poster",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-oral",
                    title="Oral Paper",
                    authors=["Bob"],
                    abstract="B",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )

                poster_job_id, poster_created = db.enqueue_download_job("paper-poster")
                oral_job_id, oral_created = db.enqueue_download_job("paper-oral")
                claimed = db.claim_next_download_job(worker_id="worker-priority", lease_seconds=30)
                poster_job = db.get_download_job(poster_job_id)
                oral_job = db.get_download_job(oral_job_id)
                poster_job_after_claim = db.get_download_job(poster_job_id)

            self.assertTrue(poster_created)
            self.assertTrue(oral_created)
            self.assertEqual(poster_job["download_priority"], 2)
            self.assertEqual(oral_job["download_priority"], 0)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["paper_id"], "paper-oral")
            self.assertEqual(claimed["download_priority"], 0)
            self.assertEqual(poster_job_after_claim["status"], "pending")

    def test_unresolved_failed_download_jobs_ignore_resolved_papers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "failed-jobs.db"
            resolved_pdf = Path(tmpdir) / "paper-resolved.pdf"
            resolved_pdf.write_bytes(b"%PDF-1.4 resolved")

            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="paper-resolved",
                    title="Resolved Paper",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-unresolved",
                    title="Unresolved Paper",
                    authors=["Bob"],
                    abstract="B",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )

                resolved_job_id, _ = db.enqueue_download_job("paper-resolved")
                db.claim_next_download_job(worker_id="worker-resolved", lease_seconds=30)
                db.fail_download_job(resolved_job_id, "timed out")
                db.update_pdf_metadata(
                    paper_id="paper-resolved",
                    pdf_path=str(resolved_pdf),
                    pdf_sha256="sha-resolved",
                    pdf_size_bytes=len(b"%PDF-1.4 resolved"),
                )

                unresolved_job_id, _ = db.enqueue_download_job("paper-unresolved")
                db.claim_next_download_job(worker_id="worker-unresolved", lease_seconds=30)
                db.fail_download_job(unresolved_job_id, "timed out")

                unresolved = db.get_unresolved_failed_download_jobs(limit=10)

            self.assertEqual(unresolved["count"], 1)
            self.assertEqual(len(unresolved["jobs"]), 1)
            self.assertEqual(unresolved["jobs"][0]["paper_id"], "paper-unresolved")
            self.assertEqual(unresolved["jobs"][0]["last_error"], "timed out")

    def test_download_job_claim_is_single_consumer_under_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "claim-race.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                db.enqueue_download_job("paper-race")

                barrier = threading.Barrier(2)

                def claim(worker_id: str):
                    barrier.wait()
                    return db.claim_next_download_job(worker_id=worker_id, lease_seconds=30)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(claim, "worker-a"),
                        executor.submit(claim, "worker-b"),
                    ]
                    results = [future.result() for future in futures]

            claimed = [result for result in results if result is not None]
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["paper_id"], "paper-race")
            self.assertEqual(claimed[0]["status"], "running")

    def test_expired_download_job_is_requeued_and_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "claim-expired.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                job_id, created = db.enqueue_download_job("paper-expired")
                first_claim = db.claim_next_download_job(worker_id="worker-a", lease_seconds=1)
                self.assertTrue(created)
                self.assertEqual(first_claim["id"], job_id)

                with db.get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE download_jobs
                        SET lease_expires_at = datetime('now', '-5 seconds')
                        WHERE id = ?
                        """,
                        (job_id,),
                    )

                second_claim = db.claim_next_download_job(worker_id="worker-b", lease_seconds=30)

            self.assertIsNotNone(second_claim)
            self.assertEqual(second_claim["id"], job_id)
            self.assertEqual(second_claim["status"], "running")
            self.assertEqual(second_claim["claimed_by"], "worker-b")
            self.assertEqual(second_claim["attempts"], 2)

    def test_expired_job_keeps_fifo_priority_over_newer_pending_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "claim-priority.db"
            with patch.dict(os.environ, _db_path_env(db_path), clear=False):
                _reset_settings_cache()
                db.migrate()
                older_job_id, created = db.enqueue_download_job("paper-older")
                first_claim = db.claim_next_download_job(worker_id="worker-a", lease_seconds=1)
                newer_job_id, newer_created = db.enqueue_download_job("paper-newer")
                self.assertTrue(created)
                self.assertTrue(newer_created)
                self.assertEqual(first_claim["id"], older_job_id)
                self.assertNotEqual(older_job_id, newer_job_id)

                with db.get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE download_jobs
                        SET lease_expires_at = datetime('now', '-5 seconds')
                        WHERE id = ?
                        """,
                        (older_job_id,),
                    )

                second_claim = db.claim_next_download_job(worker_id="worker-b", lease_seconds=30)
                pending_job = db.get_download_job(newer_job_id)

            self.assertIsNotNone(second_claim)
            self.assertEqual(second_claim["id"], older_job_id)
            self.assertEqual(second_claim["paper_id"], "paper-older")
            self.assertEqual(second_claim["attempts"], 2)
            self.assertEqual(pending_job["status"], "pending")


if __name__ == "__main__":
    unittest.main()
