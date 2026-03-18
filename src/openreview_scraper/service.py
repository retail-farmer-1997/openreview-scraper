"""Reusable service-layer operations shared by CLI and worker paths."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Callable

from . import db, openreview as orw, settings
from .models import DiscussionPost, PaperDiscussion, Review


class ServiceOperationError(RuntimeError):
    """Raised for service-layer operation errors suitable for CLI/worker surfacing."""


DownloadProgressCallback = Callable[[dict[str, object]], None]


def _datetime_to_epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def _datetime_from_epoch_ms(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000)


def _serialize_review(review: Review) -> dict[str, object | None]:
    return {
        "id": review.id,
        "reviewer": review.reviewer,
        "rating": review.rating,
        "confidence": review.confidence,
        "summary": review.summary,
        "strengths": review.strengths,
        "weaknesses": review.weaknesses,
        "questions": review.questions,
        "limitations": review.limitations,
        "soundness": review.soundness,
        "presentation": review.presentation,
        "contribution": review.contribution,
        "recommendation": review.recommendation,
        "full_text": review.full_text,
        "created_at_ms": _datetime_to_epoch_ms(review.created_at),
    }


def _serialize_discussion_post(post: DiscussionPost) -> dict[str, object | None]:
    return {
        "id": post.id,
        "reply_to": post.reply_to,
        "author": post.author,
        "content": post.content,
        "post_type": post.post_type,
        "title": post.title,
        "created_at_ms": _datetime_to_epoch_ms(post.created_at),
    }


def _review_from_cache_row(row: dict) -> Review:
    return Review(
        id=str(row["id"]),
        paper_id=str(row["paper_id"]),
        reviewer=str(row["reviewer"]),
        rating=row["rating"],
        confidence=row["confidence"],
        summary=row["summary"],
        strengths=row["strengths"],
        weaknesses=row["weaknesses"],
        questions=row["questions"],
        limitations=row["limitations"],
        soundness=row["soundness"],
        presentation=row["presentation"],
        contribution=row["contribution"],
        recommendation=row["recommendation"],
        full_text=row["full_text"],
        created_at=_datetime_from_epoch_ms(row["created_at_ms"]),
    )


def _discussion_post_from_cache_row(row: dict) -> DiscussionPost:
    return DiscussionPost(
        id=str(row["id"]),
        paper_id=str(row["paper_id"]),
        reply_to=row["reply_to"],
        author=str(row["author"]),
        content=str(row["content"]),
        post_type=str(row["post_type"]),
        created_at=_datetime_from_epoch_ms(row["created_at_ms"]),
        title=row["title"],
    )


def _rating_number(raw_rating: str | None) -> int | None:
    if not raw_rating:
        return None
    try:
        return int(str(raw_rating).split(":")[0].strip())
    except (ValueError, IndexError):
        return None


def _cache_forum_data(paper_id: str) -> tuple[int, int]:
    reviews = orw.fetch_reviews(paper_id)
    discussion = orw.fetch_discussion(paper_id)
    if discussion is None:
        raise ServiceOperationError(f"Paper discussion not found: {paper_id}")

    db.replace_paper_forum_cache(
        paper_id=paper_id,
        reviews=[_serialize_review(review) for review in reviews],
        posts=[_serialize_discussion_post(post) for post in discussion.posts],
    )
    return len(reviews), len(discussion.posts)


def get_cached_reviews(paper_id: str) -> list[Review] | None:
    """Return cached reviews for a paper, or None if no cache exists."""
    cache = db.get_paper_forum_cache(paper_id)
    if cache is None:
        return None
    rows = db.get_cached_paper_reviews(paper_id)
    return [_review_from_cache_row(row) for row in rows]


def get_cached_discussion(paper_id: str) -> PaperDiscussion | None:
    """Return cached discussion posts for a paper, or None if no cache exists."""
    cache = db.get_paper_forum_cache(paper_id)
    if cache is None:
        return None

    paper = db.get_paper(paper_id)
    if paper is None:
        return None

    posts = [_discussion_post_from_cache_row(row) for row in db.get_cached_discussion_posts(paper_id)]
    return PaperDiscussion(
        paper_id=paper_id,
        paper_title=str(paper["title"]),
        posts=posts,
        review_count=int(cache["review_count"]),
        comment_count=sum(1 for post in posts if post.post_type == "comment"),
        has_author_response=any(post.post_type == "rebuttal" for post in posts),
        has_decision=any(post.post_type == "decision" for post in posts),
    )


def get_cached_overview(paper_id: str) -> dict | None:
    """Return cached overview data for a paper, or None if no cache exists."""
    cache = db.get_paper_forum_cache(paper_id)
    if cache is None:
        return None

    paper = db.get_paper(paper_id)
    if paper is None:
        return None

    review_rows = db.get_cached_paper_reviews(paper_id)
    post_rows = db.get_cached_discussion_posts(paper_id)
    ratings = [
        rating
        for rating in (_rating_number(row["rating"]) for row in review_rows)
        if rating is not None
    ]

    return {
        "id": paper_id,
        "title": str(paper["title"]),
        "venue": str(paper["venue"]),
        "primary_area": paper["primary_area"],
        "author_count": len(paper["authors"]),
        "first_authors": paper["authors"][:3],
        "review_count": int(cache["review_count"]),
        "rating_range": f"{min(ratings)}-{max(ratings)}" if ratings else None,
        "avg_rating": sum(ratings) / len(ratings) if ratings else None,
        "comment_count": sum(1 for row in post_rows if row["post_type"] == "comment"),
        "has_author_response": any(row["post_type"] == "rebuttal" for row in post_rows),
        "has_decision": any(row["post_type"] == "decision" for row in post_rows),
        "keywords": paper["keywords"][:5] if paper["keywords"] else None,
    }


def fetch_metadata(conference: str, year: int, decision: str) -> dict:
    """Fetch and upsert paper metadata for a venue decision bucket."""
    db.migrate()

    venue_str = orw.get_venue_string(conference, year, decision)
    papers = orw.fetch_papers_by_venue(conference, year, decision)

    if not papers:
        return {
            "operation": "fetch",
            "venue": venue_str,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "total": 0,
            "failures": [],
        }

    created_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0
    failures: list[dict[str, str]] = []

    for paper in papers:
        try:
            status = db.upsert_paper(
                paper_id=paper.id,
                title=paper.title,
                authors=paper.authors,
                abstract=paper.abstract,
                venue=paper.venue,
                venueid=paper.venueid,
                primary_area=paper.primary_area,
                keywords=paper.keywords,
            )
            if status == "created":
                created_count += 1
            elif status == "updated":
                updated_count += 1
            else:
                skipped_count += 1
        except Exception as exc:
            failed_count += 1
            failures.append({"stage": f"paper:{paper.id}", "error": orw.format_error_message(exc)})

    return {
        "operation": "fetch",
        "venue": venue_str,
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "total": len(papers),
        "failures": failures,
    }


def _refresh_pdf_metadata(paper_id: str, pdf_path: Path) -> None:
    checksum, size_bytes = orw.get_pdf_integrity_metadata(pdf_path)
    db.update_pdf_metadata(
        paper_id=paper_id,
        pdf_path=str(pdf_path),
        pdf_sha256=checksum,
        pdf_size_bytes=size_bytes,
    )


def _refresh_pdf_metadata_from_artifact(paper_id: str, artifact: dict[str, object]) -> None:
    db.update_pdf_metadata(
        paper_id=paper_id,
        pdf_path=str(artifact["path"]),
        pdf_sha256=str(artifact["sha256"]),
        pdf_size_bytes=int(artifact["size_bytes"]),
    )


def _emit_download_progress(
    progress_callback: DownloadProgressCallback | None,
    payload: dict[str, object],
) -> None:
    if progress_callback is None:
        return
    progress_callback(payload)


def download_paper(
    paper_id: str,
    tags: str | None = None,
    cache_forum: bool = True,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict:
    """Download/reconcile PDF for a paper with idempotent metadata updates."""
    db.migrate()

    created_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0
    failures: list[dict[str, str]] = []
    notes: list[str] = []
    artifact: dict[str, object] | None = None
    started_at = time.perf_counter()

    try:
        _emit_download_progress(
            progress_callback,
            {
                "phase": "loading-metadata",
                "bytes_downloaded": 0,
                "total_bytes": None,
                "network_seconds": 0.0,
                "io_seconds": 0.0,
                "elapsed_seconds": 0.0,
            },
        )
        paper = db.get_paper(paper_id)
        if paper is None:
            _emit_download_progress(
                progress_callback,
                {
                    "phase": "fetching-paper-metadata",
                    "bytes_downloaded": 0,
                    "total_bytes": None,
                    "network_seconds": 0.0,
                    "io_seconds": 0.0,
                    "elapsed_seconds": time.perf_counter() - started_at,
                },
            )
            fetched = orw.fetch_paper(paper_id)
            if fetched is None:
                failed_count += 1
                failures.append({"stage": "metadata", "error": f"paper not found: {paper_id}"})
                raise ServiceOperationError(f"Paper not found: {paper_id}")

            status = db.upsert_paper(
                paper_id=fetched.id,
                title=fetched.title,
                authors=fetched.authors,
                abstract=fetched.abstract,
                venue=fetched.venue,
                venueid=fetched.venueid,
                primary_area=fetched.primary_area,
                keywords=fetched.keywords,
            )
            if status == "created":
                created_count += 1
            elif status == "updated":
                updated_count += 1
            else:
                skipped_count += 1
            paper = db.get_paper(paper_id)

        paper_title = paper["title"] if paper is not None else paper_id

        def forward_progress(update: dict[str, object]) -> None:
            _emit_download_progress(
                progress_callback,
                {
                    "paper_id": paper_id,
                    "paper_title": paper_title,
                    **update,
                },
            )

        recorded_pdf_path = paper["pdf_path"]
        runtime_settings = settings.get_settings()
        if recorded_pdf_path:
            recorded_path = Path(recorded_pdf_path)
            if recorded_path.exists():
                artifact = orw.inspect_pdf_file(recorded_path, progress_callback=forward_progress)
                checksum = str(artifact["sha256"])
                size_bytes = int(artifact["size_bytes"])
                has_same_metadata = (
                    paper.get("pdf_sha256") == checksum and paper.get("pdf_size_bytes") == size_bytes
                )
                if has_same_metadata:
                    skipped_count += 1
                    notes.append(f"already-downloaded:{recorded_pdf_path}")
                else:
                    _refresh_pdf_metadata_from_artifact(paper_id, artifact)
                    updated_count += 1
                    notes.append(f"metadata-refreshed:{recorded_pdf_path}")
            else:
                notes.append(f"missing-recorded-path:{recorded_pdf_path}")
                artifact = orw.download_pdf_artifact(
                    paper_id,
                    runtime_settings.papers_dir,
                    progress_callback=forward_progress,
                )
                _refresh_pdf_metadata_from_artifact(paper_id, artifact)
                updated_count += 1
                notes.append(f"saved:{artifact['path']}")
        else:
            artifact = orw.download_pdf_artifact(
                paper_id,
                runtime_settings.papers_dir,
                progress_callback=forward_progress,
            )
            _refresh_pdf_metadata_from_artifact(paper_id, artifact)
            updated_count += 1
            notes.append(f"saved:{artifact['path']}")

        if cache_forum and db.get_paper_forum_cache(paper_id) is None:
            try:
                _emit_download_progress(
                    progress_callback,
                    {
                        "paper_id": paper_id,
                        "paper_title": paper_title,
                        "phase": "caching-forum",
                        "bytes_downloaded": (
                            int(artifact["downloaded_bytes"]) if artifact is not None else 0
                        ),
                        "total_bytes": artifact["total_bytes"] if artifact is not None else None,
                        "network_seconds": (
                            float(artifact["network_seconds"]) if artifact is not None else 0.0
                        ),
                        "io_seconds": float(artifact["io_seconds"]) if artifact is not None else 0.0,
                        "elapsed_seconds": time.perf_counter() - started_at,
                    },
                )
                review_count, post_count = _cache_forum_data(paper_id)
            except Exception as exc:
                failed_count += 1
                failures.append({"stage": "forum-cache", "error": orw.format_error_message(exc)})
            else:
                updated_count += 1
                notes.append(f"reviews-cached:{review_count}")
                notes.append(f"discussion-cached:{post_count}")

        if tags:
            added_any = False
            for tag in tags.split(","):
                normalized = tag.strip()
                if normalized:
                    db.add_tag(paper_id, normalized)
                    added_any = True
            if added_any:
                updated_count += 1
                notes.append("tags-updated")
    except Exception as exc:
        if not isinstance(exc, ServiceOperationError):
            failed_count += 1
            failures.append({"stage": "download", "error": orw.format_error_message(exc)})
        raise

    elapsed_seconds = time.perf_counter() - started_at
    network_seconds = float(artifact["network_seconds"]) if artifact is not None else 0.0
    io_seconds = float(artifact["io_seconds"]) if artifact is not None else 0.0

    return {
        "operation": "download",
        "paper_id": paper_id,
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "failures": failures,
        "notes": notes,
        "performance": {
            "bytes_downloaded": int(artifact["downloaded_bytes"]) if artifact is not None else 0,
            "total_bytes": artifact["total_bytes"] if artifact is not None else None,
            "network_seconds": network_seconds,
            "io_seconds": io_seconds,
            "other_seconds": max(elapsed_seconds - network_seconds - io_seconds, 0.0),
            "elapsed_seconds": elapsed_seconds,
            "source": artifact["source"] if artifact is not None else None,
        },
    }
