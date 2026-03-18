"""Background worker entrypoints for queued sync execution."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import os
import socket
import time
from typing import Callable

from . import db, service, settings


DownloadStatusCallback = Callable[[dict], None]


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


def _fold_download_result(summary: dict, result: dict) -> None:
    summary["processed"] += 1
    if result["status"] == "completed":
        summary["completed"] += 1
        item_summary = result.get("summary", {})
        summary["created"] += int(item_summary.get("created", 0))
        summary["updated"] += int(item_summary.get("updated", 0))
        summary["skipped"] += int(item_summary.get("skipped", 0))
    else:
        summary["failed"] += 1
    summary["last_status"] = result["status"]


def run_parallel_download_workers(
    *,
    worker_count: int,
    max_jobs: int | None = None,
    status_interval_seconds: float = 5.0,
    status_callback: DownloadStatusCallback | None = None,
) -> dict:
    """Run queued download jobs with multiple local workers until the queue is drained."""
    if worker_count < 1:
        raise ValueError("worker_count must be >= 1")
    if status_interval_seconds < 0:
        raise ValueError("status_interval_seconds must be >= 0")
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be >= 1")

    db.migrate()

    summary = {
        "operation": "run-downloads",
        "processed": 0,
        "completed": 0,
        "failed": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "continuous": False,
        "max_jobs": max_jobs,
        "workers": worker_count,
        "last_status": "idle",
    }

    if max_jobs == 0:
        return summary

    submitted = 0
    status_timeout = None if status_interval_seconds == 0 else status_interval_seconds
    futures: dict[Future, None] = {}

    def submit(executor: ThreadPoolExecutor) -> bool:
        nonlocal submitted
        if max_jobs is not None and submitted >= max_jobs:
            return False
        futures[executor.submit(run_next_download_job)] = None
        submitted += 1
        return True

    def emit_status() -> None:
        if status_callback is None:
            return
        queue_status = db.get_download_queue_status(limit=0)
        status_callback(
            {
                "workers": worker_count,
                "processed": summary["processed"],
                "completed": summary["completed"],
                "failed": summary["failed"],
                "counts": queue_status["counts"],
            }
        )

    initial_slots = worker_count if max_jobs is None else min(worker_count, max_jobs)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="download-worker") as executor:
        for _ in range(initial_slots):
            submit(executor)

        if futures and status_callback is not None:
            emit_status()

        while futures:
            done, _ = wait(set(futures), timeout=status_timeout, return_when=FIRST_COMPLETED)
            if not done:
                emit_status()
                continue

            for future in done:
                futures.pop(future, None)
                result = future.result()
                if not result["processed"]:
                    summary["last_status"] = result["status"]
                    continue

                _fold_download_result(summary, result)
                submit(executor)

    return summary


def get_download_queue_status(limit: int = 20) -> dict:
    """Return aggregate download queue status plus recent jobs."""
    db.migrate()
    return db.get_download_queue_status(limit=limit)
