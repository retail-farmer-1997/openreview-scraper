"""Tests for service boundary extraction and worker execution path."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _install_openreview_stub() -> None:
    if "openreview" in sys.modules:
        return

    stub = types.ModuleType("openreview")

    class DummyOpenReviewException(Exception):
        pass

    class DummyOpenReviewClient:
        def __init__(self, *args, **kwargs):
            pass

    stub.OpenReviewException = DummyOpenReviewException
    stub.api = types.SimpleNamespace(OpenReviewClient=DummyOpenReviewClient)
    sys.modules["openreview"] = stub


_install_openreview_stub()

from openreview_scraper import db, service, settings, worker
from openreview_scraper.models import Paper


class ServiceWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.reset_settings_cache()

    def tearDown(self) -> None:
        settings.reset_settings_cache()

    def test_fetch_service_callable_outside_click(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "service.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
            }
            fake_paper = Paper(
                id="svc-paper",
                title="Service Paper",
                authors=["Alice"],
                abstract="A",
                venue="ICLR 2025 Oral",
                venueid="ICLR/2025",
            )

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.service.orw.fetch_papers_by_venue", return_value=[fake_paper]):
                    summary = service.fetch_metadata("ICLR", 2025, "oral")
                paper = db.get_paper("svc-paper")

            self.assertEqual(summary["created"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertIsNotNone(paper)
            self.assertEqual(paper["title"], "Service Paper")

    def test_worker_background_sync_path_is_testable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "worker.db"
            env = {"OPENREVIEW_SCRAPER_DB_PATH": str(db_path)}

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                enqueue = worker.enqueue_sync_request("ICLR", 2025, "oral")
                with patch(
                    "openreview_scraper.worker.service.fetch_metadata",
                    return_value={
                        "operation": "fetch",
                        "venue": "ICLR 2025 Oral",
                        "created": 2,
                        "updated": 0,
                        "skipped": 0,
                        "failed": 0,
                        "total": 2,
                        "failures": [],
                    },
                ):
                    result = worker.run_next_sync_job()
                job = db.get_sync_job(enqueue["job_id"])

            self.assertTrue(enqueue["created"])
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["processed"])
            self.assertEqual(job["status"], "completed")

    def test_download_worker_queue_path_is_testable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "download-worker.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
                "OPENREVIEW_SCRAPER_DOWNLOAD_JOB_LEASE_SECONDS": "60",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                db.migrate()
                db.upsert_paper(
                    paper_id="paper-download",
                    title="Download Worker Paper",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )

                enqueue = worker.enqueue_reconcile_download_requests()
                cache_forum_values: list[bool] = []

                def fake_download(
                    paper_id: str,
                    tags: str | None = None,
                    cache_forum: bool = True,
                    progress_callback=None,
                ) -> dict:
                    del tags
                    del progress_callback
                    cache_forum_values.append(cache_forum)
                    pdf_path = papers_dir / f"{paper_id}.pdf"
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(b"%PDF-1.4 worker")
                    db.update_pdf_metadata(
                        paper_id=paper_id,
                        pdf_path=str(pdf_path),
                        pdf_sha256="worker-sha",
                        pdf_size_bytes=len(b"%PDF-1.4 worker"),
                    )
                    return {
                        "operation": "download",
                        "paper_id": paper_id,
                        "created": 0,
                        "updated": 1,
                        "skipped": 0,
                        "failed": 0,
                        "failures": [],
                        "notes": [f"saved:{pdf_path}"],
                    }

                with patch("openreview_scraper.worker.service.download_paper", side_effect=fake_download):
                    result = worker.run_download_worker(max_jobs=1)

                queue_status = worker.get_download_queue_status(limit=5)
                jobs = queue_status["jobs"]

            self.assertEqual(enqueue["created"], 1)
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["request_count"], 0)
            self.assertEqual(cache_forum_values, [False])
            self.assertEqual(queue_status["counts"]["completed"], 1)
            self.assertEqual(jobs[0]["paper_id"], "paper-download")
            self.assertEqual(jobs[0]["status"], "completed")

    def test_download_worker_deduplicates_repeat_failures_by_paper(self) -> None:
        with patch(
            "openreview_scraper.worker.run_next_download_job",
            side_effect=[
                {
                    "status": "failed",
                    "processed": True,
                    "job_id": 11,
                    "paper_id": "paper-repeat",
                    "paper_title": "Repeat Failure Paper",
                    "attempts": 1,
                    "error": "download: timeout",
                },
                {
                    "status": "failed",
                    "processed": True,
                    "job_id": 12,
                    "paper_id": "paper-repeat",
                    "paper_title": "Repeat Failure Paper",
                    "attempts": 2,
                    "error": "download: timeout again",
                },
            ],
        ):
            summary = worker.run_download_worker(max_jobs=2)

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failed_attempts"], 2)
        self.assertEqual(len(summary["recent_failures"]), 1)
        self.assertEqual(summary["recent_failures"][0]["paper_id"], "paper-repeat")
        self.assertEqual(summary["recent_failures"][0]["attempts"], 2)
        self.assertEqual(summary["recent_failures"][0]["error"], "download: timeout again")

    def test_parallel_download_workers_drain_queue_and_report_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "parallel-download-worker.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
                "OPENREVIEW_SCRAPER_DOWNLOAD_JOB_LEASE_SECONDS": "60",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                db.migrate()
                for paper_id in ("paper-p1", "paper-p2", "paper-p3"):
                    db.upsert_paper(
                        paper_id=paper_id,
                        title=f"Queued {paper_id}",
                        authors=["Alice"],
                        abstract="A",
                        venue="ICLR 2025 Oral",
                        venueid="ICLR/2025",
                    )

                enqueue = worker.enqueue_reconcile_download_requests()
                status_snapshots: list[dict] = []
                progress_events: list[dict] = []
                cache_forum_values: list[bool] = []

                def fake_download(
                    paper_id: str,
                    tags: str | None = None,
                    cache_forum: bool = True,
                    progress_callback=None,
                ) -> dict:
                    del tags
                    del progress_callback
                    cache_forum_values.append(cache_forum)
                    pdf_path = papers_dir / f"{paper_id}.pdf"
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(b"%PDF-1.4 parallel")
                    db.update_pdf_metadata(
                        paper_id=paper_id,
                        pdf_path=str(pdf_path),
                        pdf_sha256=f"sha-{paper_id}",
                        pdf_size_bytes=len(b"%PDF-1.4 parallel"),
                    )
                    return {
                        "operation": "download",
                        "paper_id": paper_id,
                        "created": 0,
                        "updated": 1,
                        "skipped": 0,
                        "failed": 0,
                        "failures": [],
                        "notes": [f"saved:{pdf_path}"],
                    }

                with patch("openreview_scraper.worker.service.download_paper", side_effect=fake_download):
                    result = worker.run_parallel_download_workers(
                        worker_count=2,
                        status_interval_seconds=0.01,
                        status_callback=status_snapshots.append,
                        progress_callback=progress_events.append,
                    )

                queue_status = worker.get_download_queue_status(limit=5)

            self.assertEqual(enqueue["created"], 3)
            self.assertEqual(result["processed"], 3)
            self.assertEqual(result["completed"], 3)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["workers"], 2)
            self.assertEqual(result["target_jobs"], 3)
            self.assertEqual(result["request_count"], 0)
            self.assertIn(result["constraint"], {"io", "mixed", "network", "other", "idle"})
            self.assertTrue(status_snapshots)
            self.assertGreaterEqual(status_snapshots[-1]["target_jobs"], 3)
            self.assertIn("constraint", status_snapshots[-1]["metrics"])
            self.assertIn("request_observability", status_snapshots[-1])
            self.assertEqual(status_snapshots[-1]["request_observability"]["request_count"], 0)
            self.assertTrue(progress_events)
            self.assertTrue(any(event["paper_title"].startswith("Queued") for event in progress_events))
            self.assertEqual(cache_forum_values, [False, False, False])
            self.assertEqual(queue_status["counts"]["completed"], 3)
            self.assertEqual(queue_status["counts"]["pending"], 0)

    def test_parallel_download_workers_report_recent_failure_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "parallel-download-worker-failures.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
                "OPENREVIEW_SCRAPER_DOWNLOAD_JOB_LEASE_SECONDS": "60",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                db.migrate()
                for paper_id in ("paper-ok", "paper-fail"):
                    db.upsert_paper(
                        paper_id=paper_id,
                        title=f"Queued {paper_id}",
                        authors=["Alice"],
                        abstract="A",
                        venue="ICLR 2025 Oral",
                        venueid="ICLR/2025",
                    )

                worker.enqueue_reconcile_download_requests()
                status_snapshots: list[dict] = []
                cache_forum_values: list[bool] = []

                def fake_download(
                    paper_id: str,
                    tags: str | None = None,
                    cache_forum: bool = True,
                    progress_callback=None,
                ) -> dict:
                    del tags
                    del progress_callback
                    cache_forum_values.append(cache_forum)
                    if paper_id == "paper-fail":
                        return {
                            "operation": "download",
                            "paper_id": paper_id,
                            "created": 0,
                            "updated": 0,
                            "skipped": 0,
                            "failed": 1,
                            "failures": [
                                {
                                    "stage": "forum-cache",
                                    "error": (
                                        "{'name': 'RateLimitError', 'message': "
                                        "'Too many requests: forum API unavailable'}"
                                    ),
                                }
                            ],
                            "notes": [],
                            "performance": {
                                "bytes_downloaded": 0,
                                "total_bytes": None,
                                "network_seconds": 0.0,
                                "io_seconds": 0.0,
                                "other_seconds": 0.0,
                                "elapsed_seconds": 0.0,
                                "source": None,
                            },
                        }

                    pdf_path = papers_dir / f"{paper_id}.pdf"
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(b"%PDF-1.4 ok")
                    db.update_pdf_metadata(
                        paper_id=paper_id,
                        pdf_path=str(pdf_path),
                        pdf_sha256=f"sha-{paper_id}",
                        pdf_size_bytes=len(b"%PDF-1.4 ok"),
                    )
                    return {
                        "operation": "download",
                        "paper_id": paper_id,
                        "created": 0,
                        "updated": 1,
                        "skipped": 0,
                        "failed": 0,
                        "failures": [],
                        "notes": [f"saved:{pdf_path}"],
                        "performance": {
                            "bytes_downloaded": len(b"%PDF-1.4 ok"),
                            "total_bytes": len(b"%PDF-1.4 ok"),
                            "network_seconds": 0.0,
                            "io_seconds": 0.0,
                            "other_seconds": 0.0,
                            "elapsed_seconds": 0.0,
                            "source": "download",
                        },
                    }

                with patch("openreview_scraper.worker.service.download_paper", side_effect=fake_download):
                    summary = worker.run_parallel_download_workers(
                        worker_count=2,
                        status_interval_seconds=0.01,
                        status_callback=status_snapshots.append,
                    )

                queue_status = worker.get_download_queue_status(limit=5)

            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["completed"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["failed_attempts"], 1)
            self.assertEqual(summary["request_count"], 0)
            self.assertEqual(summary["recent_failures"][0]["paper_id"], "paper-fail")
            self.assertEqual(
                summary["recent_failures"][0]["error"],
                "forum-cache: Too many requests: forum API unavailable",
            )
            self.assertTrue(status_snapshots)
            self.assertEqual(status_snapshots[-1]["request_observability"]["request_count"], 0)
            self.assertEqual(
                status_snapshots[-1]["recent_failures"][0]["error"],
                "forum-cache: Too many requests: forum API unavailable",
            )
            self.assertEqual(cache_forum_values, [False, False])
            failed_jobs = [job for job in queue_status["jobs"] if job["paper_id"] == "paper-fail"]
            self.assertTrue(failed_jobs)
            self.assertEqual(
                failed_jobs[0]["last_error"],
                "forum-cache: Too many requests: forum API unavailable",
            )


if __name__ == "__main__":
    unittest.main()
