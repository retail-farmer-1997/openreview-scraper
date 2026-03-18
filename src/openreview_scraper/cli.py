"""CLI for managing OpenReview papers."""

from __future__ import annotations

import json
import sys

import click

from . import __version__, db, openreview as orw, service, settings, worker


TOP_LEVEL_COMMANDS = (
    "fetch",
    "abstract",
    "download",
    "list",
    "tag",
    "note",
    "show",
    "overview",
    "reviews",
    "discussion",
)
DB_COMMANDS = ("migrate", "status", "stats")
WORKER_COMMANDS = (
    "enqueue-sync",
    "run-once",
    "enqueue-download",
    "enqueue-downloads",
    "run-downloads",
    "download-status",
)


def _ensure_db_migrated() -> None:
    """Ensure the database schema is up to date before DB operations."""
    db.migrate()


def _run_network(operation):
    """Run network operations with standardized actionable CLI errors."""
    try:
        return operation()
    except orw.NetworkOperationError as exc:
        raise click.ClickException(str(exc)) from exc


def _emit_run_summary(summary: dict, json_output: bool) -> None:
    """Emit human-readable or JSON run summary."""
    if json_output:
        click.echo(json.dumps(summary, sort_keys=True))
        return

    click.echo(
        "Run summary: "
        f"created={summary['created']} "
        f"updated={summary['updated']} "
        f"skipped={summary['skipped']} "
        f"failed={summary['failed']}"
    )
    if summary.get("failures"):
        for failure in summary["failures"]:
            click.echo(f"  failure[{failure['stage']}]: {failure['error']}", err=True)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="openreview-scraper")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """OpenReview paper tools - fetch, download, and manage local paper data."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.group("db", invoke_without_command=True)
@click.pass_context
def db_commands(ctx: click.Context) -> None:
    """Database setup and migration commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@db_commands.command("migrate")
def db_migrate() -> None:
    """Apply all pending database migrations."""
    applied = db.migrate()
    if not applied:
        click.echo("Database is already up to date.")
        return

    click.echo(f"Applied {len(applied)} migration(s):")
    for version in applied:
        click.echo(f"  - {version}")


@db_commands.command("status")
def db_status() -> None:
    """Show applied and pending database migration versions."""
    applied, pending = db.get_migration_status()

    click.echo(f"Applied migrations ({len(applied)}):")
    if applied:
        for version in applied:
            click.echo(f"  - {version}")
    else:
        click.echo("  - none")

    click.echo(f"Pending migrations ({len(pending)}):")
    if pending:
        for version in pending:
            click.echo(f"  - {version}")
    else:
        click.echo("  - none")


@db_commands.command("stats")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
def db_stats(json_output: bool) -> None:
    """Show database inventory and queue counts."""
    _ensure_db_migrated()
    summary = db.get_db_stats()

    if json_output:
        click.echo(json.dumps(summary, sort_keys=True))
        return

    papers = summary["papers"]
    relations = summary["relations"]
    sync_jobs = summary["sync_jobs"]
    download_jobs = summary["download_jobs"]

    click.echo(
        "Papers: "
        f"total={papers['total']} "
        f"downloaded-recorded={papers['downloaded_recorded']} "
        f"missing-record={papers['missing_record']} "
        f"needs-reconcile={papers['needs_reconcile']} "
        f"missing-files={papers['missing_files']}"
    )
    click.echo(
        "Relations: "
        f"authors={relations['authors']} "
        f"keywords={relations['keywords']} "
        f"tags={relations['tags']} "
        f"notes={relations['notes']}"
    )
    click.echo(
        "Sync jobs: "
        f"pending={sync_jobs['pending']} "
        f"running={sync_jobs['running']} "
        f"completed={sync_jobs['completed']} "
        f"failed={sync_jobs['failed']}"
    )
    click.echo(
        "Download jobs: "
        f"pending={download_jobs['pending']} "
        f"running={download_jobs['running']} "
        f"completed={download_jobs['completed']} "
        f"failed={download_jobs['failed']}"
    )


@cli.group("worker", invoke_without_command=True)
@click.pass_context
def worker_commands(ctx: click.Context) -> None:
    """Background worker commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@worker_commands.command("enqueue-sync")
@click.argument("conference")
@click.argument("year", type=int)
@click.argument("decision")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
def worker_enqueue_sync(conference: str, year: int, decision: str, json_output: bool) -> None:
    """Enqueue a venue sync request for background worker execution."""
    result = worker.enqueue_sync_request(conference=conference, year=year, decision=decision)
    if json_output:
        click.echo(json.dumps(result, sort_keys=True))
        return

    if result["created"]:
        click.echo(f"Queued sync job #{result['job_id']} for {conference} {year} {decision}")
    else:
        click.echo(
            f"Pending sync already exists (job #{result['job_id']}) for "
            f"{conference} {year} {decision}"
        )


@worker_commands.command("run-once")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
def worker_run_once(json_output: bool) -> None:
    """Run one pending background sync job, if available."""
    result = worker.run_next_sync_job()
    if json_output:
        click.echo(json.dumps(result, sort_keys=True))
        return

    status = result["status"]
    if status == "idle":
        click.echo("No pending sync jobs.")
        return

    job_id = result.get("job_id")
    if status == "completed":
        summary = result["summary"]
        click.echo(
            f"Completed sync job #{job_id}: created={summary['created']} "
            f"updated={summary['updated']} skipped={summary['skipped']} failed={summary['failed']}"
        )
        return

    click.echo(f"Sync job #{job_id} failed: {result.get('error')}", err=True)


@worker_commands.command("enqueue-download")
@click.argument("paper_id")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
def worker_enqueue_download(paper_id: str, json_output: bool) -> None:
    """Enqueue one paper download for background processing."""
    result = worker.enqueue_download_request(paper_id)
    if json_output:
        click.echo(json.dumps(result, sort_keys=True))
        return

    if result["created"]:
        click.echo(f"Queued download job #{result['job_id']} for {paper_id}")
    else:
        click.echo(f"Active download already exists (job #{result['job_id']}) for {paper_id}")


@worker_commands.command("enqueue-downloads")
@click.option("--limit", type=click.IntRange(min=1), help="Only queue the first N candidates")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
def worker_enqueue_downloads(limit: int | None, json_output: bool) -> None:
    """Enqueue papers whose PDFs need download or metadata reconciliation."""
    result = worker.enqueue_reconcile_download_requests(limit=limit)
    if json_output:
        click.echo(json.dumps(result, sort_keys=True))
        return

    click.echo(
        f"Queued {result['created']} download job(s) from {result['candidates']} "
        f"candidate paper(s); skipped {result['skipped']} already active."
    )


@worker_commands.command("run-downloads")
@click.option(
    "--enqueue-missing",
    is_flag=True,
    help="Queue papers from the local DB whose PDFs need download or reconciliation before running",
)
@click.option(
    "--continuous",
    is_flag=True,
    help="Keep polling for new download jobs instead of exiting when the queue is empty",
)
@click.option(
    "--poll-interval-seconds",
    type=click.FloatRange(min=0.0),
    default=5.0,
    show_default=True,
    help="Idle wait time between polling attempts in continuous mode",
)
@click.option(
    "--workers",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Number of local download workers to run in parallel",
)
@click.option(
    "--status-interval-seconds",
    type=click.FloatRange(min=0.0),
    default=2.0,
    show_default=True,
    help="How often to print queue status while local workers are running; 0 disables updates",
)
@click.option(
    "--max-jobs",
    type=click.IntRange(min=1),
    help="Stop after processing N jobs even if the queue is not empty",
)
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
def worker_run_downloads(
    enqueue_missing: bool,
    continuous: bool,
    poll_interval_seconds: float,
    workers: int,
    status_interval_seconds: float,
    max_jobs: int | None,
    json_output: bool,
) -> None:
    """Run background download jobs until the queue is drained or limits are reached."""
    if continuous and workers > 1:
        raise click.ClickException("--continuous only supports --workers 1")

    enqueue_summary: dict | None = None
    if enqueue_missing:
        enqueue_summary = worker.enqueue_reconcile_download_requests()
        if not json_output:
            click.echo(
                f"Queued {enqueue_summary['created']} download job(s) from "
                f"{enqueue_summary['candidates']} candidate paper(s); skipped "
                f"{enqueue_summary['skipped']} already active."
            )

    if not json_output and not continuous and workers > 1:
        click.echo(f"Starting {workers} local download workers.")

    def emit_status(snapshot: dict) -> None:
        counts = snapshot["counts"]
        click.echo(
            "Status: "
            f"pending={counts['pending']} "
            f"running={counts['running']} "
            f"completed={counts['completed']} "
            f"failed={counts['failed']} "
            f"processed={snapshot['processed']}"
        )

    status_callback = None
    if not json_output and not continuous and workers > 1 and status_interval_seconds > 0:
        status_callback = emit_status

    if continuous:
        summary = worker.run_download_worker(
            continuous=True,
            poll_interval_seconds=poll_interval_seconds,
            max_jobs=max_jobs,
        )
        summary["workers"] = 1
    else:
        summary = worker.run_parallel_download_workers(
            worker_count=workers,
            max_jobs=max_jobs,
            status_interval_seconds=status_interval_seconds,
            status_callback=status_callback,
        )

    if enqueue_summary is not None:
        summary = {**summary, "enqueue": enqueue_summary}

    if json_output:
        click.echo(json.dumps(summary, sort_keys=True))
        return

    if summary["processed"] == 0 and not continuous:
        click.echo("No pending download jobs.")
        return

    click.echo(
        f"Processed {summary['processed']} download job(s): "
        f"completed={summary['completed']} "
        f"failed={summary['failed']} "
        f"created={summary['created']} "
        f"updated={summary['updated']} "
        f"skipped={summary['skipped']}"
    )


@worker_commands.command("download-status")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=10,
    show_default=True,
    help="Maximum number of recent jobs to show",
)
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
def worker_download_status(limit: int, json_output: bool) -> None:
    """Show download queue counts and recent jobs."""
    status = worker.get_download_queue_status(limit=limit)
    if json_output:
        click.echo(json.dumps(status, sort_keys=True))
        return

    counts = status["counts"]
    click.echo(
        "Download jobs: "
        f"pending={counts['pending']} "
        f"running={counts['running']} "
        f"completed={counts['completed']} "
        f"failed={counts['failed']}"
    )
    if not status["jobs"]:
        click.echo("Recent jobs: none")
        return

    click.echo("Recent jobs:")
    for job in status["jobs"]:
        detail = f"  #{job['id']} {job['status']} paper={job['paper_id']} attempts={job['attempts']}"
        if job.get("claimed_by"):
            detail += f" claimed_by={job['claimed_by']}"
        if job.get("last_error"):
            detail += f" error={job['last_error']}"
        click.echo(detail)


@cli.command()
@click.argument("conference")
@click.argument("year", type=int)
@click.argument("decision")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
@click.option("--run-id", help="Compatibility no-op for older scripts")
def fetch(
    conference: str,
    year: int,
    decision: str,
    json_output: bool,
    run_id: str | None,
) -> None:
    """Fetch paper metadata from OpenReview."""
    del run_id
    venue_str = orw.get_venue_string(conference, year, decision)
    click.echo(f"Fetching papers: {venue_str}")

    summary = _run_network(lambda: service.fetch_metadata(conference, year, decision))

    if summary["total"] == 0:
        click.echo("No papers found.")
        _emit_run_summary(summary, json_output=json_output)
        return

    existing_count = summary["updated"] + summary["skipped"]
    click.echo(f"Fetched {summary['created']} new papers, {existing_count} already in database.")

    _emit_run_summary(summary, json_output=json_output)

    if summary["failed"]:
        raise click.ClickException(
            f"Fetch completed with {summary['failed']} failed item(s). Re-run safely to resume."
        )


@cli.command()
@click.argument("paper_id")
def abstract(paper_id: str) -> None:
    """Show abstract for a paper."""
    _ensure_db_migrated()

    paper = db.get_paper(paper_id)
    if paper is None:
        click.echo("Paper not in database, fetching from OpenReview...")
        fetched = _run_network(lambda: orw.fetch_paper(paper_id))
        if fetched is None:
            click.echo(f"Paper not found: {paper_id}", err=True)
            return

        db.insert_paper(
            paper_id=fetched.id,
            title=fetched.title,
            authors=fetched.authors,
            abstract=fetched.abstract,
            venue=fetched.venue,
            venueid=fetched.venueid,
            primary_area=fetched.primary_area,
            keywords=fetched.keywords,
        )
        paper = db.get_paper(paper_id)

    click.echo(f"\n{paper['title']}")
    click.echo(f"Authors: {', '.join(paper['authors'][:5])}")
    if len(paper["authors"]) > 5:
        click.echo(f"         ... and {len(paper['authors']) - 5} more")
    click.echo(f"\nAbstract:\n{paper['abstract']}")


@cli.command()
@click.argument("paper_id")
@click.option("--tags", "-t", help="Comma-separated tags to add")
@click.option("--json-output", is_flag=True, help="Emit machine-readable JSON summary")
@click.option("--run-id", help="Compatibility no-op for older scripts")
def download(
    paper_id: str,
    tags: str | None,
    json_output: bool,
    run_id: str | None,
) -> None:
    """Download a paper's PDF."""
    del run_id

    try:
        summary = _run_network(lambda: service.download_paper(paper_id=paper_id, tags=tags))
    except service.ServiceOperationError as exc:
        summary = {
            "operation": "download",
            "paper_id": paper_id,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 1,
            "failures": [{"stage": "download", "error": str(exc)}],
        }
        _emit_run_summary(summary, json_output=json_output)
        raise click.ClickException(str(exc)) from exc

    for note in summary.get("notes", []):
        if note.startswith("already-downloaded:"):
            click.echo(f"Already downloaded: {note.split(':', 1)[1]}")
        elif note.startswith("metadata-refreshed:"):
            click.echo(f"Metadata refreshed for: {note.split(':', 1)[1]}")
        elif note.startswith("missing-recorded-path:"):
            click.echo(f"Recorded PDF missing, re-downloading: {note.split(':', 1)[1]}")
        elif note.startswith("saved:"):
            click.echo(f"Saved to: {note.split(':', 1)[1]}")
        elif note.startswith("reviews-cached:"):
            click.echo(f"Cached reviews: {note.split(':', 1)[1]}")
        elif note.startswith("discussion-cached:"):
            click.echo(f"Cached discussion posts: {note.split(':', 1)[1]}")
        elif note == "tags-updated":
            click.echo(f"Tags added: {tags}")

    _emit_run_summary(summary, json_output=json_output)


@cli.command("list")
@click.option("--venue", "-v", help="Filter by venue (partial match)")
@click.option("--tag", "-t", help="Filter by tag")
@click.option("--author", "-a", help="Filter by author name (partial match)")
@click.option("--keyword", "-k", help="Filter by keyword (partial match)")
@click.option("--downloaded", "-d", is_flag=True, help="Only show downloaded papers")
def list_papers(
    venue: str | None,
    tag: str | None,
    author: str | None,
    keyword: str | None,
    downloaded: bool,
) -> None:
    """List papers in the database."""
    _ensure_db_migrated()

    papers = db.list_papers(
        venue=venue,
        tag=tag,
        author=author,
        keyword=keyword,
        downloaded_only=downloaded,
    )

    if not papers:
        click.echo("No papers found.")
        return

    click.echo(f"Found {len(papers)} papers:\n")
    for paper in papers:
        downloaded_mark = "[x]" if paper["pdf_path"] else "[ ]"
        click.echo(f"{downloaded_mark} {paper['title'][:70]}")
        click.echo(f"    ID: {paper['id']}  |  {paper['venue']}")


@cli.command()
@click.argument("paper_id")
@click.argument("tags")
def tag(paper_id: str, tags: str) -> None:
    """Add tags to a paper."""
    _ensure_db_migrated()

    paper = db.get_paper(paper_id)
    if paper is None:
        click.echo(f"Paper not found in database: {paper_id}", err=True)
        return

    for tag_name in tags.split(","):
        tag_name = tag_name.strip()
        if tag_name:
            db.add_tag(paper_id, tag_name)

    click.echo(f"Tags added to: {paper['title'][:50]}...")
    click.echo(f"Current tags: {', '.join(db.get_paper_tags(paper_id))}")


@cli.command()
@click.argument("paper_id")
@click.argument("note_text")
def note(paper_id: str, note_text: str) -> None:
    """Add a note to a paper."""
    _ensure_db_migrated()

    paper = db.get_paper(paper_id)
    if paper is None:
        click.echo(f"Paper not found in database: {paper_id}", err=True)
        return

    db.add_note(paper_id, note_text)
    click.echo(f"Note added to: {paper['title'][:50]}...")


@cli.command()
@click.argument("paper_id")
def show(paper_id: str) -> None:
    """Show full details of a paper."""
    _ensure_db_migrated()

    paper = db.get_paper(paper_id)
    if paper is None:
        click.echo(f"Paper not found in database: {paper_id}", err=True)
        click.echo("Use 'openreview-scraper abstract <id>' to fetch from OpenReview.")
        return

    click.echo(f"\n{'=' * 60}")
    click.echo(f"Title: {paper['title']}")
    click.echo(f"{'=' * 60}")
    click.echo(f"ID: {paper['id']}")
    click.echo(f"Venue: {paper['venue']}")
    click.echo(f"Primary Area: {paper['primary_area'] or 'N/A'}")
    click.echo(f"\nAuthors: {', '.join(paper['authors'])}")

    if paper["keywords"]:
        click.echo(f"\nKeywords: {', '.join(paper['keywords'])}")

    click.echo(f"\nAbstract:\n{paper['abstract']}")

    tags = db.get_paper_tags(paper_id)
    if tags:
        click.echo(f"\nTags: {', '.join(tags)}")

    notes = db.get_paper_notes(paper_id)
    if notes:
        click.echo(f"\nNotes ({len(notes)}):")
        for note_row in notes:
            click.echo(f"  [{note_row['created_at']}] {note_row['content']}")

    if paper["pdf_path"]:
        click.echo(f"\nPDF: {paper['pdf_path']}")
    else:
        click.echo("\nPDF: Not downloaded")

    runtime_settings = settings.get_settings()
    click.echo(f"\nOpenReview: {runtime_settings.openreview_web_url}/forum?id={paper_id}")


@cli.command()
@click.argument("paper_id")
def overview(paper_id: str) -> None:
    """Get a quick overview of a paper with review stats."""
    _ensure_db_migrated()
    data = service.get_cached_overview(paper_id)
    if data is None:
        click.echo(f"Fetching overview for {paper_id}...")
        data = _run_network(lambda: orw.fetch_overview(paper_id))
    else:
        click.echo(f"Using cached overview for {paper_id}...")

    if data is None:
        click.echo(f"Paper not found: {paper_id}", err=True)
        return

    click.echo(f"\n{data['title']}")
    click.echo(f"{'-' * min(60, len(data['title']))}")
    click.echo(f"ID: {data['id']}")
    click.echo(f"Venue: {data['venue']}")

    if data["primary_area"]:
        click.echo(f"Area: {data['primary_area']}")

    authors_str = ", ".join(data["first_authors"])
    if data["author_count"] > 3:
        authors_str += f" (+{data['author_count'] - 3} more)"
    click.echo(f"Authors: {authors_str}")

    if data["keywords"]:
        click.echo(f"Keywords: {', '.join(data['keywords'])}")

    click.echo(f"\nReviews: {data['review_count']}")
    if data["avg_rating"]:
        click.echo(f"Ratings: {data['rating_range']} (avg: {data['avg_rating']:.1f})")

    indicators: list[str] = []
    if data["has_author_response"]:
        indicators.append("author response")
    if data["has_decision"]:
        indicators.append("decision posted")
    if data["comment_count"] > 0:
        indicators.append(f"{data['comment_count']} comments")
    if indicators:
        click.echo(f"Discussion: {' | '.join(indicators)}")

    click.echo("\nExplore further:")
    click.echo(f"  openreview-scraper abstract {paper_id}")
    click.echo(f"  openreview-scraper reviews {paper_id}")
    click.echo(f"  openreview-scraper discussion {paper_id}")


@cli.command()
@click.argument("paper_id")
@click.option("--full", "-f", is_flag=True, help="Show full review content")
@click.option("--reviewer", "-r", type=int, help="Show specific reviewer (1-indexed)")
def reviews(paper_id: str, full: bool, reviewer: int | None) -> None:
    """List reviews for a paper from OpenReview."""
    _ensure_db_migrated()
    review_list = service.get_cached_reviews(paper_id)
    if review_list is None:
        click.echo(f"Fetching reviews for {paper_id}...")
        review_list = _run_network(lambda: orw.fetch_reviews(paper_id))
    else:
        click.echo(f"Using cached reviews for {paper_id}...")

    if not review_list:
        click.echo("No reviews found for this paper.")
        return

    click.echo(f"\nFound {len(review_list)} reviews:\n")
    for index, review in enumerate(review_list, 1):
        if reviewer is not None and index != reviewer:
            continue

        click.echo(f"{'=' * 60}")
        click.echo(f"Review {index}: {review.reviewer}")
        click.echo(f"{'=' * 60}")

        if review.rating:
            click.echo(f"Rating: {review.rating}")
        if review.confidence:
            click.echo(f"Confidence: {review.confidence}")
        if review.soundness:
            click.echo(f"Soundness: {review.soundness}")
        if review.presentation:
            click.echo(f"Presentation: {review.presentation}")
        if review.contribution:
            click.echo(f"Contribution: {review.contribution}")

        if full or reviewer is not None:
            if review.summary:
                click.echo(f"\nSummary:\n{review.summary}")
            if review.strengths:
                click.echo(f"\nStrengths:\n{review.strengths}")
            if review.weaknesses:
                click.echo(f"\nWeaknesses:\n{review.weaknesses}")
            if review.questions:
                click.echo(f"\nQuestions:\n{review.questions}")
            if review.limitations:
                click.echo(f"\nLimitations:\n{review.limitations}")
        elif review.summary:
            summary_preview = review.summary[:200]
            if len(review.summary) > 200:
                summary_preview += "..."
            click.echo(f"\nSummary: {summary_preview}")

        click.echo()

    if not full and reviewer is None:
        click.echo(f"Use 'openreview-scraper reviews {paper_id} --full' for complete reviews")
        click.echo(f"Use 'openreview-scraper reviews {paper_id} -r N' for a specific reviewer")


@cli.command()
@click.argument("paper_id")
@click.option("--compact", "-c", is_flag=True, help="Compact view (no full content)")
def discussion(paper_id: str, compact: bool) -> None:
    """Show the full discussion thread for a paper."""
    _ensure_db_migrated()
    disc = service.get_cached_discussion(paper_id)
    if disc is None:
        click.echo(f"Fetching discussion for {paper_id}...")
        disc = _run_network(lambda: orw.fetch_discussion(paper_id))
    else:
        click.echo(f"Using cached discussion for {paper_id}...")

    if disc is None:
        click.echo(f"Paper not found: {paper_id}", err=True)
        return

    click.echo(f"\n{'=' * 60}")
    click.echo(f"Discussion: {disc.paper_title}")
    click.echo(f"{'=' * 60}")
    click.echo(f"\n{disc.review_count} reviews | {disc.comment_count} comments")

    flags: list[str] = []
    if disc.has_author_response:
        flags.append("author responded")
    if disc.has_decision:
        flags.append("decision posted")
    if flags:
        click.echo(f"{' | '.join(flags)}")

    click.echo(f"\n{'-' * 60}")

    for post in disc.posts:
        type_label = post.post_type.replace("_", " ").title()
        click.echo(f"\n{type_label} by {post.author}")
        if post.created_at:
            click.echo(f"  {post.created_at.strftime('%Y-%m-%d %H:%M')}")
        if post.title:
            click.echo(f"  Title: {post.title}")
        if not compact:
            content = post.content
            if len(content) > 500 and post.post_type not in ("decision", "meta_review"):
                content = content[:500] + f"... [truncated, {len(post.content)} chars total]"
            click.echo(f"\n{content}")
        click.echo(f"\n{'-' * 40}")

    runtime_settings = settings.get_settings()
    click.echo(f"\n{runtime_settings.openreview_web_url}/forum?id={paper_id}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        cli.main(args=args, prog_name="openreview-scraper", standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code)
    return 0


def main_entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    main_entry()
