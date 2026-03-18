"""Tests for OpenReview/PDF network resilience behavior."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

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


def _install_requests_stub() -> None:
    if "requests" in sys.modules:
        return

    stub = types.ModuleType("requests")

    class Timeout(Exception):
        pass

    class RequestException(Exception):
        pass

    def get(*_args, **_kwargs):
        raise RequestException("requests stub used outside patched path")

    stub.Timeout = Timeout
    stub.RequestException = RequestException
    stub.get = get
    sys.modules["requests"] = stub


_install_openreview_stub()
_install_requests_stub()

import requests

from openreview_scraper import openreview as orw, settings


class OpenReviewNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.reset_settings_cache()

    def tearDown(self) -> None:
        settings.reset_settings_cache()

    def test_fetch_paper_retries_transient_openreview_error(self) -> None:
        exc_cls = orw.openreview.OpenReviewException
        fake_note = types.SimpleNamespace(
            id="paper-1",
            content={
                "title": {"value": "Paper"},
                "authors": {"value": []},
                "abstract": {"value": ""},
                "venue": {"value": ""},
                "venueid": {"value": ""},
            },
        )

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            def get_note(self, _paper_id: str):
                self.calls += 1
                if self.calls == 1:
                    raise exc_cls("503 Service Unavailable")
                return fake_note

        fake_client = FakeClient()
        env = {
            "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "1",
            "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS": "0",
            "OPENREVIEW_SCRAPER_HTTP_RETRY_JITTER_SECONDS": "0",
        }

        with patch.dict(os.environ, env, clear=False):
            settings.reset_settings_cache()
            with patch("openreview_scraper.openreview.get_client", return_value=fake_client):
                paper = orw.fetch_paper("paper-1")

        self.assertIsNotNone(paper)
        self.assertEqual(paper.id, "paper-1")
        self.assertEqual(fake_client.calls, 2)

    def test_fetch_paper_returns_none_for_not_found(self) -> None:
        exc_cls = orw.openreview.OpenReviewException

        class FakeClient:
            def get_note(self, _paper_id: str):
                raise exc_cls("404 Not Found")

        with patch("openreview_scraper.openreview.get_client", return_value=FakeClient()):
            paper = orw.fetch_paper("missing-paper")

        self.assertIsNone(paper)

    def test_get_client_uses_repo_auth_settings(self) -> None:
        env = {
            "OPENREVIEW_SCRAPER_OPENREVIEW_API_URL": "https://api2.openreview.net",
            "OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME": "alice@example.com",
            "OPENREVIEW_SCRAPER_OPENREVIEW_PASSWORD": "secret",
            "OPENREVIEW_SCRAPER_OPENREVIEW_TOKEN": "token-123",
        }

        with patch.dict(os.environ, env, clear=False):
            settings.reset_settings_cache()
            with patch("openreview_scraper.openreview.openreview.api.OpenReviewClient") as client_cls:
                orw.get_client()

        client_cls.assert_called_once_with(
            baseurl="https://api2.openreview.net",
            username="alice@example.com",
            password="secret",
            token="token-123",
        )

    def test_fetch_papers_by_venue_uses_submission_invitation_and_filters_decision(self) -> None:
        oral_note = types.SimpleNamespace(
            id="oral-note",
            content={
                "title": {"value": "Oral Paper"},
                "authors": {"value": ["Ada"]},
                "abstract": {"value": "A"},
                "venue": {"value": "ICLR 2025 Oral"},
                "venueid": {"value": "ICLR.cc/2025/Conference/Oral"},
            },
        )
        poster_note = types.SimpleNamespace(
            id="poster-note",
            content={
                "title": {"value": "Poster Paper"},
                "authors": {"value": ["Grace"]},
                "abstract": {"value": "B"},
                "venue": {"value": "ICLR 2025 Poster"},
                "venueid": {"value": "ICLR.cc/2025/Conference/Poster"},
            },
        )
        fake_group = types.SimpleNamespace(
            content={"submission_id": {"value": "ICLR.cc/2025/Conference/-/Submission"}}
        )

        class FakeClient:
            def __init__(self) -> None:
                self.group_calls: list[str] = []
                self.note_calls: list[dict[str, str | None]] = []

            def get_group(self, group_id: str):
                self.group_calls.append(group_id)
                return fake_group

            def get_all_notes(self, *, invitation: str | None = None, content=None):
                self.note_calls.append({"invitation": invitation, "content": content})
                return [oral_note, poster_note]

        fake_client = FakeClient()

        with patch("openreview_scraper.openreview.get_client", return_value=fake_client):
            papers = orw.fetch_papers_by_venue("ICLR", 2025, "oral")

        self.assertEqual(fake_client.group_calls, ["ICLR.cc/2025/Conference"])
        self.assertEqual(
            fake_client.note_calls,
            [{"invitation": "ICLR.cc/2025/Conference/-/Submission", "content": None}],
        )
        self.assertEqual([paper.id for paper in papers], ["oral-note"])
        self.assertEqual(papers[0].venueid, "ICLR.cc/2025/Conference/Oral")

    def test_fetch_papers_by_venue_falls_back_to_default_submission_invitation(self) -> None:
        oral_note = types.SimpleNamespace(
            id="oral-note",
            content={
                "title": {"value": "Oral Paper"},
                "authors": {"value": ["Ada"]},
                "abstract": {"value": "A"},
                "venue": {"value": "ICLR 2025 Oral"},
                "venueid": {"value": "ICLR.cc/2025/Conference/Oral"},
            },
        )
        fake_group = types.SimpleNamespace(content={})

        class FakeClient:
            def __init__(self) -> None:
                self.note_invitation: str | None = None

            def get_group(self, _group_id: str):
                return fake_group

            def get_all_notes(self, *, invitation: str | None = None, content=None):
                self.note_invitation = invitation
                return [oral_note]

        fake_client = FakeClient()

        with patch("openreview_scraper.openreview.get_client", return_value=fake_client):
            papers = orw.fetch_papers_by_venue("ICLR", 2025, "oral")

        self.assertEqual(fake_client.note_invitation, "ICLR.cc/2025/Conference/-/Submission")
        self.assertEqual([paper.id for paper in papers], ["oral-note"])

    def test_fetch_papers_by_venue_falls_back_when_group_lookup_is_forbidden(self) -> None:
        exc_cls = orw.openreview.OpenReviewException
        oral_note = types.SimpleNamespace(
            id="oral-note",
            content={
                "title": {"value": "Oral Paper"},
                "authors": {"value": ["Ada"]},
                "abstract": {"value": "A"},
                "venue": {"value": "ICLR 2025 Oral"},
                "venueid": {"value": "ICLR.cc/2025/Conference/Oral"},
            },
        )

        class FakeClient:
            def __init__(self) -> None:
                self.note_invitation: str | None = None

            def get_group(self, _group_id: str):
                raise exc_cls("{'name': 'ForbiddenError', 'message': 'Forbidden'}")

            def get_all_notes(self, *, invitation: str | None = None, content=None):
                self.note_invitation = invitation
                return [oral_note]

        fake_client = FakeClient()

        with patch("openreview_scraper.openreview.get_client", return_value=fake_client):
            papers = orw.fetch_papers_by_venue("ICLR", 2025, "oral")

        self.assertEqual(fake_client.note_invitation, "ICLR.cc/2025/Conference/-/Submission")
        self.assertEqual([paper.id for paper in papers], ["oral-note"])

    def test_fetch_papers_by_venue_raises_auth_hint_when_submission_read_is_forbidden(self) -> None:
        exc_cls = orw.openreview.OpenReviewException

        class FakeClient:
            def get_group(self, _group_id: str):
                raise exc_cls("{'name': 'ForbiddenError', 'message': 'Forbidden'}")

            def get_all_notes(self, *, invitation: str | None = None, content=None):
                raise exc_cls("{'name': 'ForbiddenError', 'message': 'Forbidden'}")

        with patch.dict(os.environ, {}, clear=True):
            settings.reset_settings_cache()
            with patch("openreview_scraper.openreview.get_client", return_value=FakeClient()):
                with self.assertRaisesRegex(
                    orw.NetworkOperationError,
                    "OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME",
                ):
                    orw.fetch_papers_by_venue("ICLR", 2026, "oral")

    def test_download_pdf_rate_limit_raises_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            response = Mock()
            response.status_code = 429
            response.headers = {"Retry-After": "2"}
            response.raise_for_status = Mock()

            env = {
                "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "0",
                "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS": "0",
                "OPENREVIEW_SCRAPER_HTTP_RETRY_JITTER_SECONDS": "0",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.openreview.requests.get", return_value=response):
                    with self.assertRaises(orw.RateLimitError) as ctx:
                        orw.download_pdf("paper-429", output_dir)

        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertIn("Retry-After=2", str(ctx.exception))

    def test_download_pdf_retries_timeout_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            success_response = Mock()
            success_response.status_code = 200
            success_response.headers = {}
            success_response.content = b"%PDF-1.4 test"
            success_response.raise_for_status = Mock()

            env = {
                "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "1",
                "OPENREVIEW_SCRAPER_HTTP_TIMEOUT_SECONDS": "0.1",
                "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS": "0",
                "OPENREVIEW_SCRAPER_HTTP_RETRY_JITTER_SECONDS": "0",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch(
                    "openreview_scraper.openreview.requests.get",
                    side_effect=[requests.Timeout("timed out"), success_response],
                ) as mock_get:
                    pdf_path = orw.download_pdf("paper-timeout", output_dir)

            self.assertEqual(mock_get.call_count, 2)
            self.assertTrue(pdf_path.exists())
            self.assertEqual(pdf_path.read_bytes(), b"%PDF-1.4 test")

    def test_download_pdf_rejects_non_pdf_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            response = Mock()
            response.status_code = 200
            response.headers = {"Content-Type": "text/html"}
            response.content = b"%PDF-1.4 test"
            response.raise_for_status = Mock()

            with patch("openreview_scraper.openreview.requests.get", return_value=response):
                with self.assertRaises(orw.PDFValidationError):
                    orw.download_pdf("paper-html", output_dir)

    def test_download_pdf_rejects_invalid_body_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            response = Mock()
            response.status_code = 200
            response.headers = {"Content-Type": "application/pdf"}
            response.content = b"not-a-real-pdf"
            response.raise_for_status = Mock()

            with patch("openreview_scraper.openreview.requests.get", return_value=response):
                with self.assertRaises(orw.PDFValidationError):
                    orw.download_pdf("paper-invalid", output_dir)

    def test_download_pdf_uses_atomic_write_without_temp_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            response = Mock()
            response.status_code = 200
            response.headers = {"Content-Type": "application/pdf"}
            response.content = b"%PDF-1.4 atomic"
            response.raise_for_status = Mock()

            with patch("openreview_scraper.openreview.requests.get", return_value=response):
                pdf_path = orw.download_pdf("paper-atomic", output_dir)

            tmp_files = list(output_dir.glob("*.tmp"))
            self.assertTrue(pdf_path.exists())
            self.assertEqual(tmp_files, [])
