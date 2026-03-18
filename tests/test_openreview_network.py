"""Tests for OpenReview/PDF network resilience behavior."""

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

from openreview_scraper import openreview as orw, settings


class OpenReviewNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.reset_settings_cache()
        orw._reset_request_throttle()
        orw._reset_client_cache()

    def tearDown(self) -> None:
        settings.reset_settings_cache()
        orw._reset_request_throttle()
        orw._reset_client_cache()

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

    def test_request_throttle_spaces_session_requests(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.current = 100.0
                self.sleeps: list[float] = []

            def monotonic(self) -> float:
                return self.current

            def time(self) -> float:
                return self.current

            def sleep(self, seconds: float) -> None:
                self.sleeps.append(seconds)
                self.current += seconds

        class FakeSession:
            def __init__(self, clock: FakeClock) -> None:
                self._clock = clock
                self.calls: list[float] = []

            def request(self, method: str, url: str, *args, **kwargs):
                del method, url, args, kwargs
                self.calls.append(self._clock.monotonic())
                return types.SimpleNamespace(status_code=200, headers={})

        clock = FakeClock()
        fake_client = types.SimpleNamespace(session=FakeSession(clock))
        env = {
            "OPENREVIEW_SCRAPER_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS": "2.5",
            "OPENREVIEW_SCRAPER_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS": "0",
        }

        with patch.dict(os.environ, env, clear=False):
            settings.reset_settings_cache()
            with patch("openreview_scraper.openreview.time.monotonic", side_effect=clock.monotonic):
                with patch("openreview_scraper.openreview.time.time", side_effect=clock.time):
                    with patch("openreview_scraper.openreview.time.sleep", side_effect=clock.sleep):
                        orw._install_request_throttle(fake_client)
                        fake_client.session.request("GET", "https://api2.openreview.net/notes")
                        fake_client.session.request("GET", "https://api2.openreview.net/notes")

        self.assertEqual(fake_client.session.calls, [100.0, 102.5])
        self.assertEqual(clock.sleeps, [2.5])

    def test_rate_limit_retry_waits_until_reset_window(self) -> None:
        exc_cls = orw.openreview.OpenReviewException

        class FakeClock:
            def __init__(self) -> None:
                self.current = 50.0
                self.sleeps: list[float] = []

            def monotonic(self) -> float:
                return self.current

            def time(self) -> float:
                return self.current

            def sleep(self, seconds: float) -> None:
                self.sleeps.append(seconds)
                self.current += seconds

        clock = FakeClock()
        env = {
            "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "1",
            "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS": "0",
            "OPENREVIEW_SCRAPER_HTTP_RETRY_JITTER_SECONDS": "0",
            "OPENREVIEW_SCRAPER_OPENREVIEW_MIN_REQUEST_INTERVAL_SECONDS": "0",
            "OPENREVIEW_SCRAPER_OPENREVIEW_RATE_LIMIT_BUFFER_SECONDS": "2",
        }

        calls = 0

        def fake_operation() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise exc_cls("Too many requests: Please try again in 7 seconds")
            return "ok"

        with patch.dict(os.environ, env, clear=False):
            settings.reset_settings_cache()
            with patch("openreview_scraper.openreview.time.monotonic", side_effect=clock.monotonic):
                with patch("openreview_scraper.openreview.time.time", side_effect=clock.time):
                    with patch("openreview_scraper.openreview.time.sleep", side_effect=clock.sleep):
                        result = orw._retry_openreview_call("rate-limit-op", fake_operation)

        self.assertEqual(result, "ok")
        self.assertEqual(calls, 2)
        self.assertEqual(clock.sleeps, [9.0])

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

    def test_get_client_reuses_thread_local_authenticated_client(self) -> None:
        env = {
            "OPENREVIEW_SCRAPER_OPENREVIEW_API_URL": "https://api2.openreview.net",
            "OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME": "alice@example.com",
            "OPENREVIEW_SCRAPER_OPENREVIEW_PASSWORD": "secret",
        }

        with patch.dict(os.environ, env, clear=False):
            settings.reset_settings_cache()
            with patch("openreview_scraper.openreview.openreview.api.OpenReviewClient") as client_cls:
                first = orw.get_client()
                second = orw.get_client()

        self.assertIs(first, second)
        client_cls.assert_called_once()

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
            exc_cls = orw.openreview.OpenReviewException

            class FakeClient:
                def get_pdf(self, _paper_id: str) -> bytes:
                    raise exc_cls("429 Too Many Requests")

            env = {
                "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "0",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.openreview.get_client", return_value=FakeClient()):
                    with self.assertRaises(orw.RateLimitError) as ctx:
                        orw.download_pdf("paper-429", output_dir)

        self.assertIn("Rate-limited during 'download PDF for paper 'paper-429''", str(ctx.exception))

    def test_download_pdf_rate_limit_normalizes_dict_like_library_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            exc_cls = orw.openreview.OpenReviewException

            class FakeClient:
                def get_pdf(self, _paper_id: str) -> bytes:
                    raise exc_cls(
                        "{'name': 'RateLimitError', 'message': 'Too many requests: slow down'}"
                    )

            env = {
                "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "0",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.openreview.get_client", return_value=FakeClient()):
                    with self.assertRaises(orw.RateLimitError) as ctx:
                        orw.download_pdf("paper-429-dict", output_dir)

        self.assertIn(
            "Rate-limited during 'download PDF for paper 'paper-429-dict''",
            str(ctx.exception),
        )
        self.assertNotIn("{'name':", str(ctx.exception))

    def test_download_pdf_retries_timeout_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            exc_cls = orw.openreview.OpenReviewException
            api_calls = 0

            class FakeClient:
                def get_pdf(self, _paper_id: str) -> bytes:
                    nonlocal api_calls
                    api_calls += 1
                    if api_calls == 1:
                        raise exc_cls("timeout while waiting")
                    return b"%PDF-1.4 test"

            env = {
                "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES": "1",
                "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS": "0",
                "OPENREVIEW_SCRAPER_HTTP_RETRY_JITTER_SECONDS": "0",
            }

            with patch.dict(os.environ, env, clear=False):
                settings.reset_settings_cache()
                with patch("openreview_scraper.openreview.get_client", return_value=FakeClient()):
                    pdf_path = orw.download_pdf("paper-timeout", output_dir)

            self.assertEqual(api_calls, 2)
            self.assertTrue(pdf_path.exists())
            self.assertEqual(pdf_path.read_bytes(), b"%PDF-1.4 test")

    def test_download_pdf_rejects_invalid_body_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            class FakeClient:
                def get_pdf(self, _paper_id: str) -> bytes:
                    return b"not-a-real-pdf"

            with patch("openreview_scraper.openreview.get_client", return_value=FakeClient()):
                with self.assertRaises(orw.PDFValidationError):
                    orw.download_pdf("paper-invalid", output_dir)

    def test_download_pdf_downloads_from_api_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            class FakeClient:
                paper_id: str | None = None

                def get_pdf(self, paper_id: str) -> bytes:
                    self.paper_id = paper_id
                    return b"%PDF-1.4 via api"

            fake_client = FakeClient()

            with patch("openreview_scraper.openreview.get_client", return_value=fake_client):
                pdf_path = orw.download_pdf("paper-api", output_dir)
                self.assertEqual(fake_client.paper_id, "paper-api")
                self.assertTrue(pdf_path.exists())
                self.assertEqual(pdf_path.read_bytes(), b"%PDF-1.4 via api")

    def test_download_pdf_artifact_streams_progress_from_api_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            progress_updates: list[dict[str, object]] = []

            class FakeResponse:
                def __init__(self) -> None:
                    self.headers = {"Content-Length": str(len(b"%PDF-1.4 streamed"))}
                    self.closed = False

                def iter_content(self, chunk_size: int):
                    del chunk_size
                    yield b"%PDF-1."
                    yield b"4 streamed"

                def raise_for_status(self) -> None:
                    return None

                def close(self) -> None:
                    self.closed = True

            class FakeSession:
                def __init__(self) -> None:
                    self.calls: list[dict[str, object]] = []

                def get(
                    self,
                    url: str,
                    *,
                    params: dict[str, str],
                    headers: dict[str, str],
                    stream: bool,
                    timeout: float,
                ) -> FakeResponse:
                    self.calls.append(
                        {
                            "url": url,
                            "params": params,
                            "headers": headers,
                            "stream": stream,
                            "timeout": timeout,
                        }
                    )
                    return FakeResponse()

            fake_session = FakeSession()
            fake_client = types.SimpleNamespace(
                session=fake_session,
                pdf_url="https://api2.openreview.net/pdf",
                headers={"Accept": "application/json"},
                _OpenReviewClient__handle_response=lambda response: response,
            )

            with patch("openreview_scraper.openreview.get_client", return_value=fake_client):
                artifact = orw.download_pdf_artifact(
                    "paper-stream",
                    output_dir,
                    progress_callback=progress_updates.append,
                )
                self.assertTrue(Path(artifact["path"]).exists())
                self.assertEqual(Path(artifact["path"]).read_bytes(), b"%PDF-1.4 streamed")

        self.assertEqual(artifact["source"], "streaming-download")
        self.assertGreaterEqual(len(progress_updates), 2)
        self.assertEqual(progress_updates[-1]["bytes_downloaded"], len(b"%PDF-1.4 streamed"))
        self.assertEqual(fake_session.calls[0]["params"], {"id": "paper-stream"})
        self.assertTrue(fake_session.calls[0]["stream"])

    def test_download_pdf_uses_atomic_write_without_temp_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            class FakeClient:
                def get_pdf(self, _paper_id: str) -> bytes:
                    return b"%PDF-1.4 atomic"

            with patch("openreview_scraper.openreview.get_client", return_value=FakeClient()):
                pdf_path = orw.download_pdf("paper-atomic", output_dir)

            tmp_files = list(output_dir.glob("*.tmp"))
            self.assertTrue(pdf_path.exists())
            self.assertEqual(tmp_files, [])
