"""CLI integration tests for core command flows."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from click.testing import CliRunner


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

from openreview_scraper import __version__, db, settings
from openreview_scraper.cli import cli
from openreview_scraper.models import DiscussionPost, Paper, PaperDiscussion, Review


class CLIIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.reset_settings_cache()
        self.runner = CliRunner()

    def tearDown(self) -> None:
        settings.reset_settings_cache()

    @staticmethod
    def _extract_json_summary(output: str) -> dict:
        for line in reversed(output.splitlines()):
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                return json.loads(stripped)
        raise AssertionError(f"No JSON summary found in output:\n{output}")

    def test_version_option_matches_package_version(self) -> None:
        result = self.runner.invoke(cli, ["--version"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"openreview-scraper, version {__version__}", result.output)

    def test_fetch_then_list_covers_core_metadata_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-flow.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
            }

            fake_paper = Paper(
                id="paper-123",
                title="CLI Flow Paper",
                authors=["Alice", "Bob"],
                abstract="Test abstract",
                venue="ICLR 2025 Oral",
                venueid="ICLR/2025",
            )

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.service.orw.fetch_papers_by_venue", return_value=[fake_paper]):
                    fetch_result = self.runner.invoke(cli, ["fetch", "ICLR", "2025", "oral"])
                list_result = self.runner.invoke(cli, ["list"])
                author_result = self.runner.invoke(cli, ["list", "--author", "Alice"])

            self.assertEqual(fetch_result.exit_code, 0, fetch_result.output)
            self.assertIn("Fetched 1 new papers", fetch_result.output)
            self.assertEqual(list_result.exit_code, 0, list_result.output)
            self.assertIn("CLI Flow Paper", list_result.output)
            self.assertEqual(author_result.exit_code, 0, author_result.output)
            self.assertIn("CLI Flow Paper", author_result.output)

    def test_download_is_idempotent_after_first_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-download.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
            }

            fake_paper = Paper(
                id="paper-dl",
                title="Download Paper",
                authors=["Alice"],
                abstract="A",
                venue="ICLR 2025 Oral",
                venueid="ICLR/2025",
            )
            fake_pdf_path = papers_dir / "paper-dl.pdf"

            def fake_download(_paper_id: str, output_dir: Path) -> Path:
                output_dir.mkdir(parents=True, exist_ok=True)
                fake_pdf_path.write_bytes(b"%PDF-1.4 test")
                return fake_pdf_path

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.service.orw.fetch_paper", return_value=fake_paper):
                    with patch("openreview_scraper.service.orw.download_pdf", side_effect=fake_download):
                        with patch("openreview_scraper.service.orw.fetch_reviews", return_value=[]):
                            with patch(
                                "openreview_scraper.service.orw.fetch_discussion",
                                return_value=PaperDiscussion(
                                    paper_id="paper-dl",
                                    paper_title="Download Paper",
                                    posts=[],
                                ),
                            ):
                                first = self.runner.invoke(cli, ["download", "paper-dl"])
                second = self.runner.invoke(cli, ["download", "paper-dl"])
                paper = db.get_paper("paper-dl")

            self.assertEqual(first.exit_code, 0, first.output)
            self.assertIn("Saved to:", first.output)
            self.assertEqual(second.exit_code, 0, second.output)
            self.assertIn("Already downloaded:", second.output)
            self.assertIsNotNone(paper["pdf_sha256"])
            self.assertGreater(paper["pdf_size_bytes"], 0)

    def test_fetch_rerun_reports_idempotent_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-idempotent.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
            }

            fake_paper = Paper(
                id="paper-rerun",
                title="Idempotent Flow",
                authors=["Alice"],
                abstract="A",
                venue="ICLR 2025 Oral",
                venueid="ICLR/2025",
            )

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.service.orw.fetch_papers_by_venue", return_value=[fake_paper]):
                    first = self.runner.invoke(cli, ["fetch", "ICLR", "2025", "oral", "--json-output"])
                with patch("openreview_scraper.service.orw.fetch_papers_by_venue", return_value=[fake_paper]):
                    second = self.runner.invoke(cli, ["fetch", "ICLR", "2025", "oral", "--json-output"])

            first_summary = self._extract_json_summary(first.output)
            second_summary = self._extract_json_summary(second.output)
            self.assertEqual(first.exit_code, 0, first.output)
            self.assertEqual(second.exit_code, 0, second.output)
            self.assertEqual(first_summary["created"], 1)
            self.assertEqual(first_summary["updated"], 0)
            self.assertEqual(first_summary["skipped"], 0)
            self.assertEqual(second_summary["created"], 0)
            self.assertEqual(second_summary["updated"], 0)
            self.assertEqual(second_summary["skipped"], 1)

    def test_download_recovers_when_recorded_pdf_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-resume.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
            }

            fake_paper = Paper(
                id="paper-resume",
                title="Resume Paper",
                authors=["Alice"],
                abstract="A",
                venue="ICLR 2025 Oral",
                venueid="ICLR/2025",
            )

            first_pdf_path = papers_dir / "paper-resume.pdf"
            redownload_path = papers_dir / "paper-resume-redownload.pdf"

            def first_download(_paper_id: str, output_dir: Path) -> Path:
                output_dir.mkdir(parents=True, exist_ok=True)
                first_pdf_path.write_bytes(b"%PDF-1.4 first")
                return first_pdf_path

            def second_download(_paper_id: str, output_dir: Path) -> Path:
                output_dir.mkdir(parents=True, exist_ok=True)
                redownload_path.write_bytes(b"%PDF-1.4 redownload")
                return redownload_path

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.service.orw.fetch_paper", return_value=fake_paper):
                    with patch("openreview_scraper.service.orw.download_pdf", side_effect=first_download):
                        with patch("openreview_scraper.service.orw.fetch_reviews", return_value=[]):
                            with patch(
                                "openreview_scraper.service.orw.fetch_discussion",
                                return_value=PaperDiscussion(
                                    paper_id="paper-resume",
                                    paper_title="Resume Paper",
                                    posts=[],
                                ),
                            ):
                                first = self.runner.invoke(cli, ["download", "paper-resume"])

                first_pdf_path.unlink()

                with patch("openreview_scraper.service.orw.download_pdf", side_effect=second_download):
                    second = self.runner.invoke(cli, ["download", "paper-resume", "--json-output"])

            second_summary = self._extract_json_summary(second.output)
            self.assertEqual(first.exit_code, 0, first.output)
            self.assertEqual(second.exit_code, 0, second.output)
            self.assertEqual(second_summary["updated"], 1)
            self.assertEqual(second_summary["failed"], 0)
            self.assertIn("Recorded PDF missing, re-downloading", second.output)

    def test_worker_download_queue_and_status_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-download-worker.db"
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
                    paper_id="paper-q1",
                    title="Queued Paper 1",
                    authors=["Alice"],
                    abstract="A",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )
                db.upsert_paper(
                    paper_id="paper-q2",
                    title="Queued Paper 2",
                    authors=["Bob"],
                    abstract="B",
                    venue="ICLR 2025 Oral",
                    venueid="ICLR/2025",
                )

                enqueue = self.runner.invoke(cli, ["worker", "enqueue-downloads", "--json-output"])

                def fake_download(paper_id: str, tags: str | None = None) -> dict:
                    del tags
                    pdf_path = papers_dir / f"{paper_id}.pdf"
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(b"%PDF-1.4 queued")
                    db.update_pdf_metadata(
                        paper_id=paper_id,
                        pdf_path=str(pdf_path),
                        pdf_sha256=f"sha-{paper_id}",
                        pdf_size_bytes=len(b"%PDF-1.4 queued"),
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
                    run = self.runner.invoke(
                        cli,
                        ["worker", "run-downloads", "--max-jobs", "2", "--json-output"],
                    )

                download_status = self.runner.invoke(cli, ["worker", "download-status", "--json-output"])
                db_stats = self.runner.invoke(cli, ["db", "stats", "--json-output"])

            enqueue_summary = self._extract_json_summary(enqueue.output)
            run_summary = self._extract_json_summary(run.output)
            status_summary = self._extract_json_summary(download_status.output)
            db_summary = self._extract_json_summary(db_stats.output)

            self.assertEqual(enqueue.exit_code, 0, enqueue.output)
            self.assertEqual(run.exit_code, 0, run.output)
            self.assertEqual(download_status.exit_code, 0, download_status.output)
            self.assertEqual(db_stats.exit_code, 0, db_stats.output)
            self.assertEqual(enqueue_summary["created"], 2)
            self.assertEqual(run_summary["processed"], 2)
            self.assertEqual(run_summary["completed"], 2)
            self.assertEqual(status_summary["counts"]["completed"], 2)
            self.assertEqual(status_summary["counts"]["pending"], 0)
            self.assertEqual(db_summary["papers"]["downloaded_recorded"], 2)
            self.assertEqual(db_summary["download_jobs"]["completed"], 2)

    def test_worker_run_downloads_can_enqueue_and_drain_in_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-parallel-download-worker.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
                "OPENREVIEW_SCRAPER_DOWNLOAD_JOB_LEASE_SECONDS": "60",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                db.migrate()
                for paper_id in ("paper-b1", "paper-b2", "paper-b3"):
                    db.upsert_paper(
                        paper_id=paper_id,
                        title=f"Batch Paper {paper_id}",
                        authors=["Alice"],
                        abstract="A",
                        venue="ICLR 2025 Oral",
                        venueid="ICLR/2025",
                    )

                def fake_download(paper_id: str, tags: str | None = None) -> dict:
                    del tags
                    pdf_path = papers_dir / f"{paper_id}.pdf"
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(b"%PDF-1.4 batch")
                    db.update_pdf_metadata(
                        paper_id=paper_id,
                        pdf_path=str(pdf_path),
                        pdf_sha256=f"sha-{paper_id}",
                        pdf_size_bytes=len(b"%PDF-1.4 batch"),
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
                    run = self.runner.invoke(
                        cli,
                        [
                            "worker",
                            "run-downloads",
                            "--enqueue-missing",
                            "--workers",
                            "2",
                            "--status-interval-seconds",
                            "0.01",
                        ],
                    )

                download_status = self.runner.invoke(cli, ["worker", "download-status", "--json-output"])

            status_summary = self._extract_json_summary(download_status.output)
            self.assertEqual(run.exit_code, 0, run.output)
            self.assertIn("Queued 3 download job(s)", run.output)
            self.assertIn("Starting 2 local download workers.", run.output)
            self.assertIn("Status: ", run.output)
            self.assertIn("Processed 3 download job(s): completed=3 failed=0", run.output)
            self.assertEqual(status_summary["counts"]["completed"], 3)
            self.assertEqual(status_summary["counts"]["pending"], 0)

    def test_download_caches_forum_data_and_cli_reads_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cli-forum-cache.db"
            papers_dir = Path(tmpdir) / "papers"
            env = {
                "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
                "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
            }

            fake_paper = Paper(
                id="paper-cache",
                title="Cached Forum Paper",
                authors=["Alice", "Bob"],
                abstract="A",
                venue="ICLR 2025 Oral",
                venueid="ICLR/2025",
                primary_area="Reasoning",
                keywords=["cache", "forum"],
            )
            fake_pdf_path = papers_dir / "paper-cache.pdf"
            cached_reviews = [
                Review(
                    id="review-1",
                    paper_id="paper-cache",
                    reviewer="Reviewer 1",
                    rating="8: Strong Accept",
                    confidence="4: High",
                    summary="Promising direction",
                )
            ]
            cached_discussion = PaperDiscussion(
                paper_id="paper-cache",
                paper_title="Cached Forum Paper",
                posts=[
                    DiscussionPost(
                        id="review-1",
                        paper_id="paper-cache",
                        reply_to=None,
                        author="Reviewer 1",
                        content="Promising direction",
                        post_type="review",
                    ),
                    DiscussionPost(
                        id="comment-1",
                        paper_id="paper-cache",
                        reply_to="review-1",
                        author="Authors",
                        content="Thanks for the feedback",
                        post_type="rebuttal",
                        title="Author Response",
                    ),
                    DiscussionPost(
                        id="comment-2",
                        paper_id="paper-cache",
                        reply_to=None,
                        author="Anonymous",
                        content="Follow-up question",
                        post_type="comment",
                    ),
                ],
                review_count=1,
                comment_count=1,
                has_author_response=True,
                has_decision=False,
            )

            def fake_download(_paper_id: str, output_dir: Path) -> Path:
                output_dir.mkdir(parents=True, exist_ok=True)
                fake_pdf_path.write_bytes(b"%PDF-1.4 cached")
                return fake_pdf_path

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.service.orw.fetch_paper", return_value=fake_paper):
                    with patch("openreview_scraper.service.orw.download_pdf", side_effect=fake_download):
                        with patch("openreview_scraper.service.orw.fetch_reviews", return_value=cached_reviews):
                            with patch(
                                "openreview_scraper.service.orw.fetch_discussion",
                                return_value=cached_discussion,
                            ):
                                download_result = self.runner.invoke(cli, ["download", "paper-cache"])

                with patch(
                    "openreview_scraper.cli.orw.fetch_reviews",
                    side_effect=AssertionError("reviews should come from cache"),
                ):
                    reviews_result = self.runner.invoke(cli, ["reviews", "paper-cache"])

                with patch(
                    "openreview_scraper.cli.orw.fetch_discussion",
                    side_effect=AssertionError("discussion should come from cache"),
                ):
                    discussion_result = self.runner.invoke(cli, ["discussion", "paper-cache", "--compact"])

                with patch(
                    "openreview_scraper.cli.orw.fetch_overview",
                    side_effect=AssertionError("overview should come from cache"),
                ):
                    overview_result = self.runner.invoke(cli, ["overview", "paper-cache"])

            self.assertEqual(download_result.exit_code, 0, download_result.output)
            self.assertIn("Cached reviews: 1", download_result.output)
            self.assertIn("Cached discussion posts: 3", download_result.output)
            self.assertEqual(reviews_result.exit_code, 0, reviews_result.output)
            self.assertIn("Using cached reviews", reviews_result.output)
            self.assertIn("Reviewer 1", reviews_result.output)
            self.assertEqual(discussion_result.exit_code, 0, discussion_result.output)
            self.assertIn("Using cached discussion", discussion_result.output)
            self.assertIn("1 reviews | 1 comments", discussion_result.output)
            self.assertEqual(overview_result.exit_code, 0, overview_result.output)
            self.assertIn("Using cached overview", overview_result.output)
            self.assertIn("Ratings: 8-8 (avg: 8.0)", overview_result.output)

    def test_core_cli_no_longer_exposes_dashboard_command(self) -> None:
        result = self.runner.invoke(cli, ["dashboard"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No such command 'dashboard'", result.output)


if __name__ == "__main__":
    unittest.main()
