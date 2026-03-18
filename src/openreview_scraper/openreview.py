"""OpenReview API wrapper."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, TypeVar

import openreview
import requests

from .models import DiscussionPost, Paper, PaperDiscussion, Review
from .settings import get_settings


_T = TypeVar("_T")


class NetworkOperationError(RuntimeError):
    """Raised when an external network operation fails after retries."""


class RateLimitError(NetworkOperationError):
    """Raised when a rate-limit response persists after retries."""


class PDFValidationError(NetworkOperationError):
    """Raised when downloaded PDF content fails validation."""


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
            message = str(exc)
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
                    raise RateLimitError(
                        f"Rate-limited during '{operation_name}' after {max_attempts} "
                        "attempt(s). Retry later or increase "
                        "OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES/"
                        "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS."
                    ) from exc
                _sleep_before_retry(attempt)
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


def _retry_http_get(url: str, operation_name: str) -> requests.Response:
    runtime_settings = get_settings()
    max_attempts = runtime_settings.http_max_retries + 1

    for attempt in range(max_attempts):
        try:
            response = requests.get(url, timeout=runtime_settings.http_timeout_seconds)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    hint = f" Retry-After={retry_after}." if retry_after else ""
                    raise RateLimitError(
                        f"HTTP 429 rate-limit during '{operation_name}' after {max_attempts} "
                        f"attempt(s).{hint} Tune OPENREVIEW_SCRAPER_HTTP_MAX_RETRIES and "
                        "OPENREVIEW_SCRAPER_HTTP_RETRY_BACKOFF_SECONDS."
                    )
                _sleep_before_retry(attempt)
                continue

            response.raise_for_status()
            return response
        except requests.Timeout as exc:
            if attempt == max_attempts - 1:
                raise NetworkOperationError(
                    f"Timeout during '{operation_name}' after {max_attempts} attempt(s). "
                    "Increase OPENREVIEW_SCRAPER_HTTP_TIMEOUT_SECONDS or reduce retries."
                ) from exc
            _sleep_before_retry(attempt)
        except requests.RequestException as exc:
            message = str(exc)
            is_last_attempt = attempt == max_attempts - 1
            if _is_transient(message) and not is_last_attempt:
                _sleep_before_retry(attempt)
                continue
            if is_last_attempt:
                raise NetworkOperationError(
                    f"HTTP request failed during '{operation_name}' after {max_attempts} "
                    f"attempt(s): {message}"
                ) from exc
            raise NetworkOperationError(
                f"HTTP request failed during '{operation_name}': {message}"
            ) from exc

    raise NetworkOperationError(f"HTTP retry loop exhausted for '{operation_name}'")


def _validate_pdf_response_headers(response: requests.Response, operation_name: str) -> None:
    content_type = (response.headers.get("Content-Type") or "").lower()
    if (
        content_type
        and "application/pdf" not in content_type
        and "application/octet-stream" not in content_type
    ):
        raise PDFValidationError(
            f"Invalid content type during '{operation_name}': {content_type!r}. "
            "Expected PDF response."
        )


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
    content = pdf_path.read_bytes()
    _validate_pdf_bytes(content, f"validate existing file '{pdf_path.name}'")
    checksum = hashlib.sha256(content).hexdigest()
    return checksum, len(content)


def get_client() -> openreview.api.OpenReviewClient:
    """Get an OpenReview API client."""
    runtime_settings = get_settings()
    return openreview.api.OpenReviewClient(
        baseurl=runtime_settings.openreview_api_url,
        username=runtime_settings.openreview_username,
        password=runtime_settings.openreview_password,
        token=runtime_settings.openreview_token,
    )


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


def download_pdf(paper_id: str, output_dir: Path) -> Path:
    """Download a paper's PDF to the specified directory."""
    runtime_settings = get_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{paper_id}.pdf"

    if pdf_path.exists():
        get_pdf_integrity_metadata(pdf_path)
        return pdf_path

    url = f"{runtime_settings.openreview_web_url}/pdf?id={paper_id}"
    operation_name = f"download PDF for paper '{paper_id}'"
    response = _retry_http_get(url, operation_name)
    _validate_pdf_response_headers(response, operation_name)
    content = response.content
    _validate_pdf_bytes(content, operation_name)

    tmp_path_obj: Path | None = None
    tmp_file = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=output_dir,
        prefix=f"{paper_id}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with tmp_file as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        tmp_path_obj = Path(tmp_file.name)
        tmp_path_obj.replace(pdf_path)
    finally:
        if tmp_path_obj is not None and tmp_path_obj.exists():
            tmp_path_obj.unlink()

    get_pdf_integrity_metadata(pdf_path)
    return pdf_path


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
