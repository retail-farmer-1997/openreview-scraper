"""Background worker entrypoints for queued sync execution."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import os
import socket
import threading
import time
from typing import Callable

from . import db, service, settings


DownloadStatusCallback = Callable[[dict], None]
DownloadProgressCallback = Callable[[dict], None]


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
        error = service.orw.format_error_message(exc)
        db.fail_sync_job(job_id, error)
        return {
            "status": "failed",
            "processed": True,
            "job_id": job_id,
            "error": error,
        }


def _paper_display_title(paper_id: str) -> str:
    paper = db.get_paper(paper_id)
    if paper is None:
        return paper_id
    return str(paper.get("title") or paper_id)


def _emit_download_progress(
    progress_callback: DownloadProgressCallback | None,
    payload: dict[str, object],
) -> None:
    if progress_callback is None:
        return
    progress_callback(payload)


def _download_failure_error(summary: dict[str, object]) -> str:
    failures = summary.get("failures")
    if not isinstance(failures, list) or not failures:
        failed = int(summary.get("failed", 0) or 0)
        return f"{failed} download item(s) failed" if failed else "download failed"

    formatted: list[str] = []
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        stage = str(failure.get("stage") or "").strip()
        error = service.orw.format_error_message(failure.get("error") or "").strip()
        if stage and error:
            formatted.append(f"{stage}: {error}")
        elif error:
            formatted.append(error)
        elif stage:
            formatted.append(stage)

    if not formatted:
        failed = int(summary.get("failed", 0) or 0)
        return f"{failed} download item(s) failed" if failed else "download failed"
    if len(formatted) == 1:
        return formatted[0]
    preview = "; ".join(formatted[:2])
    if len(formatted) > 2:
        return f"{preview}; +{len(formatted) - 2} more"
    return preview


def _record_recent_failure(
    recent_failures: dict[str, dict[str, object]],
    result: dict[str, object],
) -> None:
    paper_id = str(result.get("paper_id") or "")
    if not paper_id:
        return

    failure = {
        "paper_id": paper_id,
        "paper_title": str(result.get("paper_title") or paper_id),
        "job_id": int(result.get("job_id", 0) or 0),
        "attempts": int(result.get("attempts", 0) or 0),
        "error": str(result.get("error") or "download failed"),
    }
    recent_failures.pop(paper_id, None)
    recent_failures[paper_id] = failure
    while len(recent_failures) > 5:
        oldest_paper_id = next(iter(recent_failures))
        recent_failures.pop(oldest_paper_id, None)


def run_next_download_job(
    *,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict:
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
    paper_title = _paper_display_title(paper_id)

    def forward_progress(update: dict[str, object]) -> None:
        _emit_download_progress(
            progress_callback,
            {
                "kind": "progress",
                "job_id": job_id,
                "paper_id": paper_id,
                "paper_title": paper_title,
                "status": "running",
                **update,
            },
        )

    forward_progress(
        {
            "phase": "claimed",
            "bytes_downloaded": 0,
            "total_bytes": None,
            "network_seconds": 0.0,
            "io_seconds": 0.0,
            "elapsed_seconds": 0.0,
        }
    )
    try:
        summary = service.download_paper(paper_id=paper_id, progress_callback=forward_progress)
        if summary["failed"] > 0:
            error = _download_failure_error(summary)
            db.fail_download_job(job_id, error)
            _emit_download_progress(
                progress_callback,
                {
                    "kind": "finished",
                    "job_id": job_id,
                    "paper_id": paper_id,
                    "paper_title": paper_title,
                    "status": "failed",
                    "phase": "failed",
                    "error": error,
                    "performance": summary.get("performance", {}),
                },
            )
            return {
                "status": "failed",
                "processed": True,
                "job_id": job_id,
                "paper_id": paper_id,
                "paper_title": paper_title,
                "attempts": int(job["attempts"]),
                "error": error,
                "summary": summary,
            }

        db.complete_download_job(job_id)
        _emit_download_progress(
            progress_callback,
            {
                "kind": "finished",
                "job_id": job_id,
                "paper_id": paper_id,
                "paper_title": paper_title,
                "status": "completed",
                "phase": "completed",
                "performance": summary.get("performance", {}),
            },
        )
        return {
            "status": "completed",
            "processed": True,
            "job_id": job_id,
            "paper_id": paper_id,
            "paper_title": paper_title,
            "attempts": int(job["attempts"]),
            "summary": summary,
        }
    except Exception as exc:
        error = service.orw.format_error_message(exc)
        db.fail_download_job(job_id, error)
        _emit_download_progress(
            progress_callback,
            {
                "kind": "finished",
                "job_id": job_id,
                "paper_id": paper_id,
                "paper_title": paper_title,
                "status": "failed",
                "phase": "failed",
                "error": error,
            },
        )
        return {
            "status": "failed",
            "processed": True,
            "job_id": job_id,
            "paper_id": paper_id,
            "paper_title": paper_title,
            "attempts": int(job["attempts"]),
            "error": error,
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
    failed_attempts = 0
    failed_papers: set[str] = set()
    recent_failures: dict[str, dict[str, object]] = {}

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
            failed_attempts += 1
            paper_id = str(result.get("paper_id") or "")
            if paper_id and paper_id not in failed_papers:
                failed_papers.add(paper_id)
                failed += 1
            _record_recent_failure(recent_failures, result)

    return {
        "operation": "run-downloads",
        "processed": processed,
        "completed": completed,
        "failed": failed,
        "failed_attempts": failed_attempts,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "continuous": continuous,
        "max_jobs": max_jobs,
        "last_status": last_result["status"] if last_result is not None else "idle",
        "target_jobs": None,
        "recent_failures": list(recent_failures.values()),
        "bytes_downloaded": 0,
        "network_seconds": 0.0,
        "io_seconds": 0.0,
        "other_seconds": 0.0,
    }


def _fold_download_result(
    summary: dict,
    result: dict,
    *,
    failed_papers: set[str],
    recent_failures: dict[str, dict[str, object]],
) -> None:
    summary["processed"] += 1
    if result["status"] == "completed":
        summary["completed"] += 1
        item_summary = result.get("summary", {})
        summary["created"] += int(item_summary.get("created", 0))
        summary["updated"] += int(item_summary.get("updated", 0))
        summary["skipped"] += int(item_summary.get("skipped", 0))
        performance = item_summary.get("performance", {})
        summary["bytes_downloaded"] += int(performance.get("bytes_downloaded", 0))
        summary["network_seconds"] += float(performance.get("network_seconds", 0.0))
        summary["io_seconds"] += float(performance.get("io_seconds", 0.0))
        summary["other_seconds"] += float(performance.get("other_seconds", 0.0))
    else:
        summary["failed_attempts"] += 1
        paper_id = str(result.get("paper_id") or "")
        if paper_id and paper_id not in failed_papers:
            failed_papers.add(paper_id)
            summary["failed"] += 1
        _record_recent_failure(recent_failures, result)
        summary["recent_failures"] = list(recent_failures.values())
    summary["last_status"] = result["status"]


def _constraint_label(network_seconds: float, io_seconds: float, other_seconds: float) -> str:
    totals = {
        "network": max(network_seconds, 0.0),
        "io": max(io_seconds, 0.0),
        "other": max(other_seconds, 0.0),
    }
    dominant_name, dominant_value = max(totals.items(), key=lambda item: item[1])
    total_seconds = sum(totals.values())
    if total_seconds <= 0 or dominant_value <= 0:
        return "idle"
    if dominant_value / total_seconds < 0.55:
        return "mixed"
    return dominant_name


def _build_download_metrics(
    summary: dict,
    *,
    active_jobs: list[dict[str, object]],
    elapsed_seconds: float,
) -> dict[str, object]:
    bytes_downloaded = int(summary["bytes_downloaded"])
    network_seconds = float(summary["network_seconds"])
    io_seconds = float(summary["io_seconds"])
    other_seconds = float(summary["other_seconds"])

    for job in active_jobs:
        bytes_downloaded += int(job.get("bytes_downloaded", 0))
        network = float(job.get("network_seconds", 0.0))
        io_time = float(job.get("io_seconds", 0.0))
        elapsed = float(job.get("elapsed_seconds", 0.0))
        network_seconds += network
        io_seconds += io_time
        other_seconds += max(elapsed - network - io_time, 0.0)

    papers_per_minute = 0.0
    bytes_per_second = 0.0
    if elapsed_seconds > 0:
        papers_per_minute = (float(summary["processed"]) / elapsed_seconds) * 60.0
        bytes_per_second = bytes_downloaded / elapsed_seconds

    return {
        "bytes_downloaded": bytes_downloaded,
        "network_seconds": network_seconds,
        "io_seconds": io_seconds,
        "other_seconds": other_seconds,
        "elapsed_seconds": elapsed_seconds,
        "papers_per_minute": papers_per_minute,
        "bytes_per_second": bytes_per_second,
        "constraint": _constraint_label(network_seconds, io_seconds, other_seconds),
    }


def run_parallel_download_workers(
    *,
    worker_count: int,
    max_jobs: int | None = None,
    status_interval_seconds: float = 5.0,
    status_callback: DownloadStatusCallback | None = None,
    progress_callback: DownloadProgressCallback | None = None,
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
        "failed_attempts": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "continuous": False,
        "max_jobs": max_jobs,
        "workers": worker_count,
        "last_status": "idle",
        "target_jobs": 0,
        "recent_failures": [],
        "bytes_downloaded": 0,
        "network_seconds": 0.0,
        "io_seconds": 0.0,
        "other_seconds": 0.0,
    }

    if max_jobs == 0:
        return summary

    submitted = 0
    status_timeout = None if status_interval_seconds == 0 else status_interval_seconds
    futures: dict[Future, int] = {}
    active_jobs: dict[int, dict[str, object]] = {}
    active_jobs_lock = threading.Lock()
    failed_papers: set[str] = set()
    recent_failures: dict[str, dict[str, object]] = {}
    started_at = time.perf_counter()
    target_jobs = db.count_claimable_download_jobs()
    if max_jobs is not None:
        target_jobs = min(target_jobs, max_jobs)
    summary["target_jobs"] = target_jobs

    def slot_progress_callback(slot_id: int) -> DownloadProgressCallback:
        def callback(update: dict[str, object]) -> None:
            event = {"slot": slot_id, **update}
            with active_jobs_lock:
                if event["kind"] == "finished":
                    active_jobs.pop(slot_id, None)
                else:
                    active_jobs[slot_id] = event
            _emit_download_progress(progress_callback, event)

        return callback

    def submit(executor: ThreadPoolExecutor, slot_id: int) -> bool:
        nonlocal submitted
        if submitted >= target_jobs:
            return False
        futures[
            executor.submit(
                run_next_download_job,
                progress_callback=slot_progress_callback(slot_id),
            )
        ] = slot_id
        submitted += 1
        return True

    def emit_status() -> None:
        if status_callback is None:
            return
        queue_status = db.get_download_queue_status(limit=0)
        with active_jobs_lock:
            active_snapshot = [active_jobs[key].copy() for key in sorted(active_jobs)]
        elapsed_seconds = time.perf_counter() - started_at
        status_callback(
            {
                "workers": worker_count,
                "processed": summary["processed"],
                "completed": summary["completed"],
                "failed": summary["failed"],
                "failed_attempts": summary["failed_attempts"],
                "target_jobs": target_jobs,
                "counts": queue_status["counts"],
                "active_jobs": active_snapshot,
                "recent_failures": list(summary["recent_failures"]),
                "completed_performance": {
                    "bytes_downloaded": int(summary["bytes_downloaded"]),
                    "network_seconds": float(summary["network_seconds"]),
                    "io_seconds": float(summary["io_seconds"]),
                    "other_seconds": float(summary["other_seconds"]),
                },
                "metrics": _build_download_metrics(
                    summary,
                    active_jobs=active_snapshot,
                    elapsed_seconds=elapsed_seconds,
                ),
            }
        )

    emit_status()
    if target_jobs == 0:
        summary.update(_build_download_metrics(summary, active_jobs=[], elapsed_seconds=0.0))
        return summary

    initial_slots = min(worker_count, target_jobs)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="download-worker") as executor:
        for slot_id in range(initial_slots):
            submit(executor, slot_id)

        while futures:
            done, _ = wait(set(futures), timeout=status_timeout, return_when=FIRST_COMPLETED)
            if not done:
                emit_status()
                continue

            for future in done:
                slot_id = futures.pop(future)
                result = future.result()
                if not result["processed"]:
                    summary["last_status"] = result["status"]
                    submit(executor, slot_id)
                    continue

                _fold_download_result(
                    summary,
                    result,
                    failed_papers=failed_papers,
                    recent_failures=recent_failures,
                )
                submit(executor, slot_id)

            emit_status()

    elapsed_seconds = time.perf_counter() - started_at
    summary.update(_build_download_metrics(summary, active_jobs=[], elapsed_seconds=elapsed_seconds))
    emit_status()
    return summary


def get_download_queue_status(limit: int = 20) -> dict:
    """Return aggregate download queue status plus recent jobs."""
    db.migrate()
    return db.get_download_queue_status(limit=limit)
