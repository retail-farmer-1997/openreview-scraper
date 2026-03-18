"""OpenReview API wrapper."""

from __future__ import annotations

import ast
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import requests
import threading
import tempfile
import time
from typing import Any, Callable, TypeVar

import openreview

from .models import DiscussionPost, Paper, PaperDiscussion, Review
from .settings import get_settings


_T = TypeVar("_T")
DownloadProgressCallback = Callable[[dict[str, object]], None]
DOWNLOAD_CHUNK_SIZE_BYTES = 64 * 1024
_RATE_LIMIT_SECONDS_PATTERN = re.compile(
    r"try again in ([0-9]+(?:\.[0-9]+)?) seconds",
    flags=re.IGNORECASE,
)


class NetworkOperationError(RuntimeError):
    """Raised when an external network operation fails after retries."""


class RateLimitError(NetworkOperationError):
    """Raised when a rate-limit response persists after retries."""


class PDFValidationError(NetworkOperationError):
    """Raised when downloaded PDF content fails validation."""


def format_error_message(error: Any) -> str:
    """Normalize OpenReview/library errors into a concise human-readable message."""
    if isinstance(error, BaseException):
        if len(error.args) == 1:
            return format_error_message(error.args[0])
        return str(error)

    if isinstance(error, dict):
        message = error.get("message")
        if message not in (None, ""):
            return format_error_message(message)
        fallback = error.get("error")
        if fallback not in (None, ""):
            return format_error_message(fallback)
        name = error.get("name")
        if name not in (None, ""):
            return str(name)
        return str(error)

    text = str(error).strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return text
        if isinstance(parsed, dict):
            return format_error_message(parsed)
    return text


def _parse_rate_limit_payload(message: str) -> dict[str, object] | None:
    stripped = message.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        payload = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _parse_reset_epoch(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        numeric_value = float(value)
        if numeric_value > 10_000_000_000:
            return numeric_value / 1000.0
        return numeric_value

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    try:
        numeric_value = float(text)
    except ValueError:
        return None
    if numeric_value > 10_000_000_000:
        return numeric_value / 1000.0
    return numeric_value


def _rate_limit_wait_seconds(message: str) -> float | None:
    payload = _parse_rate_limit_payload(message)
    if payload is not None:
        details = payload.get("details")
        if isinstance(details, dict):
            reset_epoch = _parse_reset_epoch(details.get("resetTime"))
            if reset_epoch is not None:
                return max(reset_epoch - time.time(), 0.0)

    match = _RATE_LIMIT_SECONDS_PATTERN.search(message)
    if match is None:
        return None
    return max(float(match.group(1)), 0.0)


class _OpenReviewRequestThrottle:
    """Process-local throttle for outbound OpenReview HTTP requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                min_interval = get_settings().openreview_min_request_interval_seconds
                wait_seconds = max(self._next_request_at - now, self._blocked_until - now, 0.0)
                if wait_seconds <= 0:
                    if min_interval > 0:
                        self._next_request_at = max(self._next_request_at, now) + min_interval
                    return
            time.sleep(wait_seconds)

    def block_for(self, wait_seconds: float) -> None:
        if wait_seconds <= 0:
            return
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + wait_seconds)
            self._next_request_at = max(self._next_request_at, self._blocked_until)

    def note_rate_limit(self, message: str) -> float | None:
        wait_seconds = _rate_limit_wait_seconds(message)
        if wait_seconds is None:
            return None
        wait_seconds += get_settings().openreview_rate_limit_buffer_seconds
        self.block_for(wait_seconds)
        return wait_seconds

    def reset(self) -> None:
        with self._lock:
            self._next_request_at = 0.0
            self._blocked_until = 0.0


_OPENREVIEW_REQUEST_THROTTLE = _OpenReviewRequestThrottle()
_OPENREVIEW_CLIENT_LOCAL = threading.local()


def _reset_request_throttle() -> None:
    _OPENREVIEW_REQUEST_THROTTLE.reset()


def _reset_client_cache() -> None:
    for attribute in ("client", "cache_key"):
        if hasattr(_OPENREVIEW_CLIENT_LOCAL, attribute):
            delattr(_OPENREVIEW_CLIENT_LOCAL, attribute)


def _is_rate_limited(message: str) -> bool:
    lowered = message.lower()
    return "429" in lowered or "rate limit" in lowered or "too many requests" in lowered


def _is_not_found(message: str) -> bool:
    lowered = message.lower()
    return "404" in lowered or "not found" in lowered


def _is_transient(message: str) -> bool:
    lowered = message.lower()
    transient_signals = (
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "503",
        "502",
        "504",
        "500",
    )
    return any(signal in lowered for signal in transient_signals)


def _is_forbidden(message: str) -> bool:
    lowered = message.lower()
    return "forbidden" in lowered or "403" in lowered


def _retry_delay(attempt: int) -> float:
    runtime_settings = get_settings()
    backoff = runtime_settings.http_retry_backoff_seconds * (2**attempt)
    jitter = runtime_settings.http_retry_jitter_seconds * (attempt + 1)
    return backoff + jitter


def _sleep_before_retry(attempt: int) -> None:
    delay = _retry_delay(attempt)
    if delay > 0:
        time.sleep(delay)


def _sleep_before_rate_limit_retry(message: str, attempt: int) -> None:
    wait_seconds = _OPENREVIEW_REQUEST_THROTTLE.note_rate_limit(message)
    if wait_seconds is None:
        _sleep_before_retry(attempt)
        return
    time.sleep(max(wait_seconds, _retry_delay(attempt)))


def _install_request_throttle(client: Any) -> None:
    session = getattr(client, "session", None)
    if session is None or getattr(session, "_openreview_scraper_throttled", False):
        return

    original_request = getattr(session, "request", None)
    if original_request is None:
        return

    def throttled_request(method: str, url: str, *args, **kwargs):
        _OPENREVIEW_REQUEST_THROTTLE.acquire()
        response = original_request(method, url, *args, **kwargs)
        if getattr(response, "status_code", None) == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    _OPENREVIEW_REQUEST_THROTTLE.block_for(
                        float(retry_after) + get_settings().openreview_rate_limit_buffer_seconds
                    )
                except ValueError:
                    pass
        return response

    session.request = throttled_request
    session._openreview_scraper_throttled = True


def _retry_openreview_call(
    operation_name: str,
    operation: Callable[[], _T],
    *,
    allow_not_found: bool = False,
) -> _T | None:
    runtime_settings = get_settings()
    max_attempts = runtime_settings.http_max_retries + 1

    for attempt in range(max_attempts):
        try:
            return operation()
        except openreview.OpenReviewException as exc:
            message = format_error_message(exc)
            is_last_attempt = attempt == max_attempts - 1

            if allow_not_found and _is_not_found(message):
                return None

            if _is_forbidden(message):
                auth_configured = bool(
                    runtime_settings.openreview_token
                    or runtime_settings.openreview_username
                    or runtime_settings.openreview_password
                )
                if auth_configured:
                    raise NetworkOperationError(
                        f"OpenReview denied access during '{operation_name}': {message}. "
                        "Configured credentials or token may not have permission for this venue "
                        "or resource."
                    ) from exc
                raise NetworkOperationError(
                    f"OpenReview denied anonymous access during '{operation_name}': {message}. "
                    "This venue may require authenticated OpenReview access. Set "
                    "OPENREVIEW_SCRAPER_OPENREVIEW_USERNAME/"
                    "OPENREVIEW_SCRAPER_OPENREVIEW_PASSWORD and retry. "
                    "If you already have an OpenReview session token, "
                    "OPENREVIEW_SCRAPER_OPENREVIEW_TOKEN is also accepted "
                    "(legacy RESEARCH_* vars are also accepted)."
                ) from exc

            if _is_rate_limited(message):
                if is_last_attempt:
                    _OPENREVIEW_REQUEST_THROTTLE.note_rate_limit(message)
                    raise RateLimitError(
                        f"Rate-limited during '{operation_name}' after {max_attempts} "
                        "attempt(s). Retry later or increase "
                        "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES/"
                        "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS."
                    ) from exc
                _sleep_before_rate_limit_retry(message, attempt)
                continue

            if _is_transient(message):
                if is_last_attempt:
                    raise NetworkOperationError(
                        f"Transient OpenReview failure during '{operation_name}' after "
                        f"{max_attempts} attempt(s): {message}. "
                        "Check connectivity or tune OPENREVIEW_SCRAPER_HTTP_TIMEOUT_SECONDS."
                    ) from exc
                _sleep_before_retry(attempt)
                continue

            raise NetworkOperationError(
                f"OpenReview request failed during '{operation_name}': {message}"
            ) from exc

    raise NetworkOperationError(f"OpenReview retry loop exhausted for '{operation_name}'")


def _validate_pdf_bytes(content: bytes, operation_name: str) -> None:
    if not content:
        raise PDFValidationError(f"Empty PDF body during '{operation_name}'.")
    if not content.startswith(b"%PDF-"):
        raise PDFValidationError(
            f"Invalid PDF body signature during '{operation_name}'. "
            "Expected bytes to start with '%PDF-'."
        )


def get_pdf_integrity_metadata(pdf_path: Path) -> tuple[str, int]:
    """Validate a local PDF and return (sha256, size_bytes)."""
    artifact = inspect_pdf_file(pdf_path)
    return str(artifact["sha256"]), int(artifact["size_bytes"])


def _emit_download_progress(
    progress_callback: DownloadProgressCallback | None,
    payload: dict[str, object],
) -> None:
    if progress_callback is None:
        return
    progress_callback(payload)


def _parse_content_length(raw_value: str | None) -> int | None:
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def inspect_pdf_file(
    pdf_path: Path,
    *,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, object]:
    """Stream-validate a local PDF and return integrity/performance metadata."""
    operation_name = f"validate existing file '{pdf_path.name}'"
    total_bytes = pdf_path.stat().st_size
    checksum = hashlib.sha256()
    header = bytearray()
    size_bytes = 0
    io_seconds = 0.0
    started_at = time.perf_counter()

    _emit_download_progress(
        progress_callback,
        {
            "phase": "validating-local-file",
            "bytes_downloaded": 0,
            "total_bytes": total_bytes,
            "network_seconds": 0.0,
            "io_seconds": 0.0,
            "elapsed_seconds": 0.0,
            "source": "existing-file",
        },
    )

    with pdf_path.open("rb") as handle:
        while True:
            io_started_at = time.perf_counter()
            chunk = handle.read(DOWNLOAD_CHUNK_SIZE_BYTES)
            if chunk:
                checksum.update(chunk)
                size_bytes += len(chunk)
                remaining_header_bytes = max(0, 5 - len(header))
                if remaining_header_bytes:
                    header.extend(chunk[:remaining_header_bytes])
            io_seconds += time.perf_counter() - io_started_at

            if not chunk:
                break

            _emit_download_progress(
                progress_callback,
                {
                    "phase": "validating-local-file",
                    "bytes_downloaded": size_bytes,
                    "total_bytes": total_bytes,
                    "network_seconds": 0.0,
                    "io_seconds": io_seconds,
                    "elapsed_seconds": time.perf_counter() - started_at,
                    "source": "existing-file",
                },
            )

    _validate_pdf_bytes(bytes(header), operation_name)
    elapsed_seconds = time.perf_counter() - started_at
    return {
        "path": pdf_path,
        "sha256": checksum.hexdigest(),
        "size_bytes": size_bytes,
        "downloaded_bytes": size_bytes,
        "total_bytes": total_bytes,
        "network_seconds": 0.0,
        "io_seconds": io_seconds,
        "elapsed_seconds": elapsed_seconds,
        "source": "existing-file",
    }


def _download_pdf_bytes_via_api_client(
    paper_id: str,
    operation_name: str,
    *,
    client: Any | None = None,
) -> bytes:
    """Download raw PDF bytes from the authenticated OpenReview API client."""
    client = get_client() if client is None else client
    content = _retry_openreview_call(operation_name, lambda: client.get_pdf(paper_id))
    if not isinstance(content, (bytes, bytearray)):
        raise PDFValidationError(
            f"Unexpected API PDF payload type during '{operation_name}': {type(content)!r}"
        )
    content_bytes = bytes(content)
    _validate_pdf_bytes(content_bytes, operation_name)
    return content_bytes


def _write_pdf_bytes_to_disk(
    paper_id: str,
    output_dir: Path,
    content: bytes,
    operation_name: str,
    *,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, object]:
    pdf_path = output_dir / f"{paper_id}.pdf"
    checksum = hashlib.sha256(content).hexdigest()
    total_bytes = len(content)
    io_seconds = 0.0
    started_at = time.perf_counter()
    tmp_path_obj: Path | None = None

    _emit_download_progress(
        progress_callback,
        {
            "phase": "downloading",
            "bytes_downloaded": 0,
            "total_bytes": total_bytes,
            "network_seconds": 0.0,
            "io_seconds": 0.0,
            "elapsed_seconds": 0.0,
            "source": "buffered-download",
        },
    )

    tmp_file = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=output_dir,
        prefix=f"{paper_id}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with tmp_file as handle:
            io_started_at = time.perf_counter()
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            io_seconds += time.perf_counter() - io_started_at

        _emit_download_progress(
            progress_callback,
            {
                "phase": "downloading",
                "bytes_downloaded": total_bytes,
                "total_bytes": total_bytes,
                "network_seconds": 0.0,
                "io_seconds": io_seconds,
                "elapsed_seconds": time.perf_counter() - started_at,
                "source": "buffered-download",
            },
        )

        tmp_path_obj = Path(tmp_file.name)
        io_started_at = time.perf_counter()
        tmp_path_obj.replace(pdf_path)
        io_seconds += time.perf_counter() - io_started_at
    finally:
        if tmp_path_obj is not None and tmp_path_obj.exists():
            tmp_path_obj.unlink()

    return {
        "path": pdf_path,
        "sha256": checksum,
        "size_bytes": total_bytes,
        "downloaded_bytes": total_bytes,
        "total_bytes": total_bytes,
        "network_seconds": 0.0,
        "io_seconds": io_seconds,
        "elapsed_seconds": time.perf_counter() - started_at,
        "source": "buffered-download",
    }


def _download_pdf_artifact_via_buffered_client(
    client: Any,
    paper_id: str,
    output_dir: Path,
    operation_name: str,
    *,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, object]:
    network_started_at = time.perf_counter()
    content = _download_pdf_bytes_via_api_client(paper_id, operation_name, client=client)
    artifact = _write_pdf_bytes_to_disk(
        paper_id,
        output_dir,
        content,
        operation_name,
        progress_callback=progress_callback,
    )
    artifact["network_seconds"] = time.perf_counter() - network_started_at - float(
        artifact["io_seconds"]
    )
    artifact["elapsed_seconds"] = time.perf_counter() - network_started_at
    return artifact


def _download_pdf_artifact_via_streaming_client(
    client: Any,
    paper_id: str,
    output_dir: Path,
    operation_name: str,
    *,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, object]:
    runtime_settings = get_settings()
    headers = client.headers.copy()
    headers["content-type"] = "application/pdf"
    handle_response = getattr(client, "_OpenReviewClient__handle_response", None)

    def operation() -> dict[str, object]:
        response: requests.Response | None = None
        tmp_path_obj: Path | None = None
        started_at = time.perf_counter()
        network_seconds = 0.0
        io_seconds = 0.0
        downloaded_bytes = 0
        checksum = hashlib.sha256()
        header = bytearray()

        try:
            request_started_at = time.perf_counter()
            response = client.session.get(
                client.pdf_url,
                params={"id": paper_id},
                headers=headers,
                stream=True,
                timeout=runtime_settings.http_timeout_seconds,
            )
            network_seconds += time.perf_counter() - request_started_at

            if callable(handle_response):
                response = handle_response(response)
            else:
                response.raise_for_status()

            total_bytes = _parse_content_length(response.headers.get("Content-Length"))
            _emit_download_progress(
                progress_callback,
                {
                    "phase": "downloading",
                    "bytes_downloaded": 0,
                    "total_bytes": total_bytes,
                    "network_seconds": network_seconds,
                    "io_seconds": io_seconds,
                    "elapsed_seconds": time.perf_counter() - started_at,
                    "source": "streaming-download",
                },
            )

            tmp_file = tempfile.NamedTemporaryFile(
                mode="wb",
                dir=output_dir,
                prefix=f"{paper_id}.",
                suffix=".tmp",
                delete=False,
            )
            with tmp_file as handle:
                iterator = response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE_BYTES)
                while True:
                    wait_started_at = time.perf_counter()
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        break
                    except requests.RequestException as exc:
                        raise openreview.OpenReviewException(str(exc)) from exc
                    network_seconds += time.perf_counter() - wait_started_at

                    if not chunk:
                        continue

                    io_started_at = time.perf_counter()
                    remaining_header_bytes = max(0, 5 - len(header))
                    if remaining_header_bytes:
                        header.extend(chunk[:remaining_header_bytes])
                    handle.write(chunk)
                    checksum.update(chunk)
                    downloaded_bytes += len(chunk)
                    io_seconds += time.perf_counter() - io_started_at

                    _emit_download_progress(
                        progress_callback,
                        {
                            "phase": "downloading",
                            "bytes_downloaded": downloaded_bytes,
                            "total_bytes": total_bytes,
                            "network_seconds": network_seconds,
                            "io_seconds": io_seconds,
                            "elapsed_seconds": time.perf_counter() - started_at,
                            "source": "streaming-download",
                        },
                    )

                io_started_at = time.perf_counter()
                handle.flush()
                os.fsync(handle.fileno())
                io_seconds += time.perf_counter() - io_started_at

            _validate_pdf_bytes(bytes(header), operation_name)

            pdf_path = output_dir / f"{paper_id}.pdf"
            tmp_path_obj = Path(tmp_file.name)
            io_started_at = time.perf_counter()
            tmp_path_obj.replace(pdf_path)
            io_seconds += time.perf_counter() - io_started_at

            return {
                "path": pdf_path,
                "sha256": checksum.hexdigest(),
                "size_bytes": downloaded_bytes,
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "network_seconds": network_seconds,
                "io_seconds": io_seconds,
                "elapsed_seconds": time.perf_counter() - started_at,
                "source": "streaming-download",
            }
        except requests.RequestException as exc:
            raise openreview.OpenReviewException(str(exc)) from exc
        finally:
            if response is not None:
                response.close()
            if tmp_path_obj is not None and tmp_path_obj.exists():
                tmp_path_obj.unlink()

    return _retry_openreview_call(operation_name, operation)


def get_client() -> openreview.api.OpenReviewClient:
    """Get an OpenReview API client."""
    runtime_settings = get_settings()
    cache_key = (
        runtime_settings.openreview_api_url,
        runtime_settings.openreview_username,
        runtime_settings.openreview_password,
        runtime_settings.openreview_token,
    )
    cached_client = getattr(_OPENREVIEW_CLIENT_LOCAL, "client", None)
    cached_key = getattr(_OPENREVIEW_CLIENT_LOCAL, "cache_key", None)
    if cached_client is not None and cached_key == cache_key:
        return cached_client

    client = openreview.api.OpenReviewClient(
        baseurl=runtime_settings.openreview_api_url,
        username=runtime_settings.openreview_username,
        password=runtime_settings.openreview_password,
        token=runtime_settings.openreview_token,
    )
    _install_request_throttle(client)
    _OPENREVIEW_CLIENT_LOCAL.client = client
    _OPENREVIEW_CLIENT_LOCAL.cache_key = cache_key
    return client


def _unwrap_openreview_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _normalized_text(value: Any) -> str:
    text = str(value or "")
    return " ".join(text.replace("_", " ").replace("-", " ").replace("/", " ").split()).lower()


def get_venue_group_id(conference: str, year: int) -> str:
    """Resolve a conference/year pair to the canonical OpenReview group id."""
    conference = conference.upper()

    if conference == "ICLR":
        owner = "ICLR.cc"
    elif conference == "NEURIPS":
        owner = "NeurIPS.cc"
    elif conference == "ICML":
        owner = "ICML.cc"
    else:
        raise NetworkOperationError(
            f"Unsupported conference for invitation-based fetch: {conference}. "
            "Supported conferences: ICLR, NeurIPS, ICML."
        )

    return f"{owner}/{year}/Conference"


def _extract_submission_invitation(group: Any) -> str | None:
    content = getattr(group, "content", None)
    if not isinstance(content, dict):
        return None

    invitation = _unwrap_openreview_value(content.get("submission_id"))
    if invitation:
        return str(invitation)
    return None


def _default_submission_invitation(conference: str, year: int) -> str:
    return f"{get_venue_group_id(conference, year)}/-/Submission"


def _resolve_submission_invitation(
    client: openreview.api.OpenReviewClient,
    conference: str,
    year: int,
) -> str:
    group_id = get_venue_group_id(conference, year)
    fallback_invitation = _default_submission_invitation(conference, year)

    try:
        group = _retry_openreview_call(
            f"fetch venue group '{group_id}'",
            lambda: client.get_group(group_id),
        )
    except NetworkOperationError as exc:
        if "Forbidden" in str(exc):
            return fallback_invitation
        raise

    invitation = _extract_submission_invitation(group)
    if invitation:
        return invitation

    return fallback_invitation


def _note_matches_decision(note: Any, decision: str) -> bool:
    content = getattr(note, "content", None)
    if not isinstance(content, dict):
        return False

    decision_token = _normalized_text(decision)
    if not decision_token:
        return False

    venue = _normalized_text(_unwrap_openreview_value(content.get("venue")))
    venueid = _normalized_text(_unwrap_openreview_value(content.get("venueid")))

    return decision_token in venue or decision_token in venueid


def get_venue_string(conference: str, year: int, decision: str) -> str:
    """Map user-friendly input to OpenReview venue string."""
    conference = conference.upper()

    if conference == "NEURIPS":
        return f"NeurIPS {year} {decision.lower()}"
    if conference == "ICLR":
        return f"ICLR {year} {decision.capitalize()}"
    if conference == "ICML":
        return f"ICML {year} {decision.capitalize()}"
    return f"{conference} {year} {decision.capitalize()}"


def fetch_papers_by_venue(conference: str, year: int, decision: str) -> list[Paper]:
    """Fetch papers from OpenReview matching conference/year/decision."""
    client = get_client()
    invitation = _resolve_submission_invitation(client, conference, year)

    notes = list(
        _retry_openreview_call(
            f"fetch submissions for invitation '{invitation}'",
            lambda: client.get_all_notes(invitation=invitation),
        )
    )

    matching_notes = [note for note in notes if _note_matches_decision(note, decision)]
    return [Paper.from_openreview_note(note) for note in matching_notes]


def fetch_paper(paper_id: str) -> Paper | None:
    """Fetch a single paper by ID."""
    client = get_client()
    note = _retry_openreview_call(
        f"fetch paper '{paper_id}'",
        lambda: client.get_note(paper_id),
        allow_not_found=True,
    )
    if note is None:
        return None
    return Paper.from_openreview_note(note)


def download_pdf_artifact(
    paper_id: str,
    output_dir: Path,
    *,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, object]:
    """Download a paper's PDF and return file/performance metadata."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{paper_id}.pdf"

    if pdf_path.exists():
        return inspect_pdf_file(pdf_path, progress_callback=progress_callback)

    operation_name = f"download PDF for paper '{paper_id}'"
    client = get_client()
    has_streaming_client = all(
        hasattr(client, attribute) for attribute in ("session", "pdf_url", "headers")
    )
    if has_streaming_client:
        return _download_pdf_artifact_via_streaming_client(
            client,
            paper_id,
            output_dir,
            operation_name,
            progress_callback=progress_callback,
        )
    return _download_pdf_artifact_via_buffered_client(
        client,
        paper_id,
        output_dir,
        operation_name,
        progress_callback=progress_callback,
    )


def download_pdf(paper_id: str, output_dir: Path) -> Path:
    """Download a paper's PDF to the specified directory."""
    artifact = download_pdf_artifact(paper_id, output_dir)
    return Path(artifact["path"])


def fetch_reviews(paper_id: str) -> list[Review]:
    """Fetch all reviews for a paper."""
    client = get_client()

    notes = list(
        _retry_openreview_call(
            f"fetch reviews for paper '{paper_id}'",
            lambda: client.get_all_notes(forum=paper_id),
        )
    )

    reviews: list[Review] = []
    for note in notes:
        if note.id == paper_id:
            continue

        invitations = note.invitations or []
        invitation = invitations[0] if invitations else ""
        if "Review" in invitation and "Meta" not in invitation:
            reviews.append(Review.from_openreview_note(note))

    reviews.sort(key=lambda review: review.created_at or 0)
    return reviews


def fetch_discussion(paper_id: str) -> PaperDiscussion | None:
    """Fetch the full discussion thread for a paper."""
    client = get_client()

    paper_note = _retry_openreview_call(
        f"fetch discussion paper note '{paper_id}'",
        lambda: client.get_note(paper_id),
        allow_not_found=True,
    )
    if paper_note is None:
        return None

    paper = Paper.from_openreview_note(paper_note)

    notes = list(
        _retry_openreview_call(
            f"fetch discussion forum notes for '{paper_id}'",
            lambda: client.get_all_notes(forum=paper_id),
        )
    )

    posts: list[DiscussionPost] = []
    review_count = 0
    comment_count = 0
    has_author_response = False
    has_decision = False

    for note in notes:
        if note.id == paper_id:
            continue

        post = DiscussionPost.from_openreview_note(note)
        posts.append(post)

        if post.post_type == "review":
            review_count += 1
        elif post.post_type == "comment":
            comment_count += 1
        elif post.post_type == "rebuttal":
            has_author_response = True
        elif post.post_type == "decision":
            has_decision = True

    posts.sort(key=lambda post: post.created_at or 0)

    return PaperDiscussion(
        paper_id=paper_id,
        paper_title=paper.title,
        posts=posts,
        review_count=review_count,
        comment_count=comment_count,
        has_author_response=has_author_response,
        has_decision=has_decision,
    )


def fetch_overview(paper_id: str) -> dict | None:
    """Fetch a quick overview of a paper for progressive discovery."""
    client = get_client()

    paper_note = _retry_openreview_call(
        f"fetch overview paper note '{paper_id}'",
        lambda: client.get_note(paper_id),
        allow_not_found=True,
    )
    if paper_note is None:
        return None

    paper = Paper.from_openreview_note(paper_note)

    notes = list(
        _retry_openreview_call(
            f"fetch overview forum notes for '{paper_id}'",
            lambda: client.get_all_notes(forum=paper_id),
        )
    )

    review_count = 0
    ratings: list[int] = []
    comment_count = 0
    has_author_response = False
    has_decision = False

    for note in notes:
        if note.id == paper_id:
            continue

        invitations = note.invitations or []
        invitation = invitations[0] if invitations else ""
        content = note.content or {}

        if "Review" in invitation and "Meta" not in invitation:
            review_count += 1
            rating_val = content.get("rating", {})
            if isinstance(rating_val, dict):
                rating_val = rating_val.get("value")
            if rating_val:
                try:
                    rating_num = int(str(rating_val).split(":")[0].strip())
                    ratings.append(rating_num)
                except (ValueError, IndexError):
                    pass
        elif "Author" in invitation or "Rebuttal" in invitation:
            has_author_response = True
        elif "Decision" in invitation:
            has_decision = True
        elif "Comment" in invitation:
            comment_count += 1

    return {
        "id": paper_id,
        "title": paper.title,
        "venue": paper.venue,
        "primary_area": paper.primary_area,
        "author_count": len(paper.authors),
        "first_authors": paper.authors[:3],
        "review_count": review_count,
        "rating_range": f"{min(ratings)}-{max(ratings)}" if ratings else None,
        "avg_rating": sum(ratings) / len(ratings) if ratings else None,
        "comment_count": comment_count,
        "has_author_response": has_author_response,
        "has_decision": has_decision,
        "keywords": paper.keywords[:5] if paper.keywords else None,
    }
