"""Background worker entrypoints for queued sync execution."""

from __future__ import annotations

import os
import socket
import time

from . import db, service, settings


def _worker_id() -> str:
    hostname = socket.gethostname() or "localhost"
    return f"{hostname}:{os.getpid()}"


def enqueue_sync_request(conference: str, year: int, decision: str) -> dict:
    """Queue a sync request for background processing."""
    db.migrate()
    job_id, created = db.enqueue_sync_job(conference=conference, year=year, decision=decision)
    return {
        "job_id": job_id,
        "created": created,
        "conference": conference,
        "year": year,
        "decision": decision,
    }


def enqueue_download_request(paper_id: str) -> dict:
    """Queue a download job for one paper."""
    db.migrate()
    job_id, created = db.enqueue_download_job(paper_id)
    return {"job_id": job_id, "created": created, "paper_id": paper_id}


def enqueue_reconcile_download_requests(limit: int | None = None) -> dict:
    """Queue papers whose PDFs need download or metadata reconciliation."""
    db.migrate()
    result = db.enqueue_reconcile_download_jobs(limit=limit)
    return {"operation": "enqueue-downloads", "limit": limit, **result}


def run_next_sync_job() -> dict:
    """Run a single queued sync job, if available."""
    db.migrate()
    job = db.claim_next_sync_job()
    if job is None:
        return {"status": "idle", "processed": False}

    job_id = int(job["id"])
    try:
        summary = service.fetch_metadata(
            conference=job["conference"],
            year=int(job["year"]),
            decision=job["decision"],
        )
        if summary["failed"] > 0:
            error = f"{summary['failed']} fetch item(s) failed"
            db.fail_sync_job(job_id, error)
            return {
                "status": "failed",
                "processed": True,
                "job_id": job_id,
                "error": error,
                "summary": summary,
            }

        db.complete_sync_job(job_id)
        return {
            "status": "completed",
            "processed": True,
            "job_id": job_id,
            "summary": summary,
        }
    except Exception as exc:
        db.fail_sync_job(job_id, str(exc))
        return {
            "status": "failed",
            "processed": True,
            "job_id": job_id,
            "error": str(exc),
        }


def run_next_download_job() -> dict:
    """Run a single queued download job, if available."""
    db.migrate()
    runtime_settings = settings.get_settings()
    job = db.claim_next_download_job(
        worker_id=_worker_id(),
        lease_seconds=runtime_settings.download_job_lease_seconds,
    )
    if job is None:
        return {"status": "idle", "processed": False}

    job_id = int(job["id"])
    paper_id = str(job["paper_id"])
    try:
        summary = service.download_paper(paper_id=paper_id)
        if summary["failed"] > 0:
            error = f"{summary['failed']} download item(s) failed"
            db.fail_download_job(job_id, error)
            return {
                "status": "failed",
                "processed": True,
                "job_id": job_id,
                "paper_id": paper_id,
                "error": error,
                "summary": summary,
            }

        db.complete_download_job(job_id)
        return {
            "status": "completed",
            "processed": True,
            "job_id": job_id,
            "paper_id": paper_id,
            "summary": summary,
        }
    except Exception as exc:
        db.fail_download_job(job_id, str(exc))
        return {
            "status": "failed",
            "processed": True,
            "job_id": job_id,
            "paper_id": paper_id,
            "error": str(exc),
        }


def run_download_worker(
    *,
    continuous: bool = False,
    poll_interval_seconds: float = 5.0,
    max_jobs: int | None = None,
) -> dict:
    """Run download jobs until the queue is drained or limits are reached."""
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be >= 0")
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be >= 1")

    processed = 0
    completed = 0
    failed = 0
    created = 0
    updated = 0
    skipped = 0
    last_result: dict | None = None

    while max_jobs is None or processed < max_jobs:
        result = run_next_download_job()
        last_result = result

        if not result["processed"]:
            if continuous:
                time.sleep(poll_interval_seconds)
                continue
            break

        processed += 1
        if result["status"] == "completed":
            completed += 1
            summary = result.get("summary", {})
            created += int(summary.get("created", 0))
            updated += int(summary.get("updated", 0))
            skipped += int(summary.get("skipped", 0))
        else:
            failed += 1

    return {
        "operation": "run-downloads",
        "processed": processed,
        "completed": completed,
        "failed": failed,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "continuous": continuous,
        "max_jobs": max_jobs,
        "last_status": last_result["status"] if last_result is not None else "idle",
    }


def get_download_queue_status(limit: int = 20) -> dict:
    """Return aggregate download queue status plus recent jobs."""
    db.migrate()
    return db.get_download_queue_status(limit=limit)
