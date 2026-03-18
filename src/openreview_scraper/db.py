"""SQLite database for paper management."""

from __future__ import annotations

import hashlib
from importlib.abc import Traversable
from importlib import resources
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache

try:
    from .settings import get_settings
except ModuleNotFoundError:

    @dataclass(frozen=True)
    class _Settings:
        db_path: Path
        db_busy_timeout_ms: int


    def _read_env(env: dict[str, str], key: str) -> str | None:
        raw = env.get(key)
        if raw is None:
            return None
        value = raw.strip()
        if not value:
            raise ValueError(f"{key} cannot be empty")
        return value


    def _int_setting(env: dict[str, str], key: str, default: int, *, min_value: int) -> int:
        raw = _read_env(env, key)
        if raw is None:
            return default
        value = int(raw)
        if value < min_value:
            raise ValueError(f"{key} must be >= {min_value}, got: {value}")
        return value


    @lru_cache(maxsize=1)
    def get_settings() -> _Settings:
        env = os.environ
        db_path = _read_env(env, "OPENREVIEW_SCRAPER_DB_PATH")
        if db_path is None:
            db_path = _read_env(env, "RESEARCH_DB_PATH") or "openreview-scraper.db"

        db_busy_timeout_ms = _int_setting(
            env,
            "OPENREVIEW_SCRAPER_DB_BUSY_TIMEOUT_MS",
            5000,
            min_value=1,
        )
        if "OPENREVIEW_SCRAPER_DB_BUSY_TIMEOUT_MS" not in env:
            db_busy_timeout_ms = _int_setting(
                env,
                "RESEARCH_DB_BUSY_TIMEOUT_MS",
                db_busy_timeout_ms,
                min_value=1,
            )

        return _Settings(db_path=Path(db_path).resolve(), db_busy_timeout_ms=db_busy_timeout_ms)


SCHEMA_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

NORMALIZED_RELATION_TABLES = (
    "authors",
    "paper_authors",
    "keyword_terms",
    "paper_keywords",
)
JOB_STATUSES = ("pending", "running", "completed", "failed")
NORMALIZED_RELATIONS_BACKFILL_VERSION = "data:normalized-relations-backfill-v1"
DOWNLOAD_PRIORITY_ORAL = 0
DOWNLOAD_PRIORITY_SPOTLIGHT = 1
DOWNLOAD_PRIORITY_POSTER = 2
DOWNLOAD_PRIORITY_DEFAULT = 3


class _ManagedConnection(sqlite3.Connection):
    """sqlite3 connection that closes on context-manager exit."""

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating DB if needed."""
    runtime_settings = get_settings()
    db_path = runtime_settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_seconds = runtime_settings.db_busy_timeout_ms / 1000
    conn = sqlite3.connect(db_path, timeout=timeout_seconds, factory=_ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {runtime_settings.db_busy_timeout_ms}")
    return conn


def _list_migration_files() -> list[Traversable]:
    migrations_dir = resources.files("openreview_scraper").joinpath("migrations")
    files = sorted(
        (
            resource
            for resource in migrations_dir.iterdir()
            if resource.is_file() and resource.name.endswith(".sql")
        ),
        key=lambda resource: resource.name,
    )
    if not files:
        raise RuntimeError(f"no migration files found in {migrations_dir}")
    return files


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _begin_immediate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _applied_migrations(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _normalized_tables_exist(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name IN (?, ?, ?, ?)
        """,
        NORMALIZED_RELATION_TABLES,
    ).fetchall()
    return len(rows) == len(NORMALIZED_RELATION_TABLES)


def _ensure_author_id(conn: sqlite3.Connection, author_name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO authors (name) VALUES (?)", (author_name,))
    row = conn.execute("SELECT id FROM authors WHERE name = ?", (author_name,)).fetchone()
    return row[0]


def _ensure_keyword_id(conn: sqlite3.Connection, keyword_term: str) -> int:
    conn.execute("INSERT OR IGNORE INTO keyword_terms (term) VALUES (?)", (keyword_term,))
    row = conn.execute("SELECT id FROM keyword_terms WHERE term = ?", (keyword_term,)).fetchone()
    return row[0]


def _sync_paper_relations(
    conn: sqlite3.Connection,
    paper_id: str,
    authors: list[str],
    keywords: list[str],
) -> None:
    if not _normalized_tables_exist(conn):
        return

    conn.execute("DELETE FROM paper_authors WHERE paper_id = ?", (paper_id,))
    for author_order, author_name in enumerate(authors):
        normalized = author_name.strip()
        if not normalized:
            continue
        author_id = _ensure_author_id(conn, normalized)
        conn.execute(
            """
            INSERT OR REPLACE INTO paper_authors (paper_id, author_id, author_order)
            VALUES (?, ?, ?)
            """,
            (paper_id, author_id, author_order),
        )

    conn.execute("DELETE FROM paper_keywords WHERE paper_id = ?", (paper_id,))
    for keyword in keywords:
        normalized = keyword.strip()
        if not normalized:
            continue
        keyword_id = _ensure_keyword_id(conn, normalized)
        conn.execute(
            "INSERT OR REPLACE INTO paper_keywords (paper_id, keyword_id) VALUES (?, ?)",
            (paper_id, keyword_id),
        )


def _backfill_normalized_relations(conn: sqlite3.Connection) -> None:
    if not _normalized_tables_exist(conn):
        return

    rows = conn.execute("SELECT id, authors, keywords FROM papers").fetchall()
    for row in rows:
        authors = json.loads(row["authors"]) if row["authors"] else []
        keywords = json.loads(row["keywords"]) if row["keywords"] else []
        _sync_paper_relations(conn, row["id"], authors, keywords)


def _ensure_normalized_relations_backfill(
    conn: sqlite3.Connection,
    applied: dict[str, str],
) -> None:
    if not _normalized_tables_exist(conn):
        return
    if NORMALIZED_RELATIONS_BACKFILL_VERSION in applied:
        return

    _backfill_normalized_relations(conn)
    conn.execute(
        "INSERT INTO schema_migrations (version, checksum) VALUES (?, ?)",
        (
            NORMALIZED_RELATIONS_BACKFILL_VERSION,
            _checksum(NORMALIZED_RELATIONS_BACKFILL_VERSION),
        ),
    )


def migrate() -> list[str]:
    """Apply all pending versioned schema migrations."""
    with get_connection() as conn:
        conn.execute(SCHEMA_MIGRATIONS_TABLE_SQL)

        applied = _applied_migrations(conn)
        newly_applied: list[str] = []

        for migration_path in _list_migration_files():
            version = migration_path.name
            sql = migration_path.read_text(encoding="utf-8")
            checksum = _checksum(sql)

            existing_checksum = applied.get(version)
            if existing_checksum is not None:
                if existing_checksum != checksum:
                    raise RuntimeError(
                        f"migration checksum mismatch for {version}: "
                        "applied migration file was modified"
                    )
                continue

            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (?, ?)",
                (version, checksum),
            )
            newly_applied.append(version)

        _ensure_normalized_relations_backfill(conn, applied)

        return newly_applied


def get_migration_status() -> tuple[list[str], list[str]]:
    """Return lists of applied and pending migrations."""
    with get_connection() as conn:
        conn.execute(SCHEMA_MIGRATIONS_TABLE_SQL)
        applied_map = _applied_migrations(conn)

    all_migrations = [path.name for path in _list_migration_files()]
    applied = [name for name in all_migrations if name in applied_map]
    pending = [name for name in all_migrations if name not in applied_map]
    return applied, pending


def init_db() -> None:
    """Backward-compatible alias for running migrations."""
    migrate()


def paper_exists(paper_id: str) -> bool:
    """Check if a paper already exists in the database."""
    with get_connection() as conn:
        result = conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return result is not None


def insert_paper(
    paper_id: str,
    title: str,
    authors: list[str],
    abstract: str,
    venue: str,
    venueid: str,
    primary_area: str | None = None,
    keywords: list[str] | None = None,
) -> bool:
    """Insert a paper. Returns True only when a new row is created."""
    result = upsert_paper(
        paper_id=paper_id,
        title=title,
        authors=authors,
        abstract=abstract,
        venue=venue,
        venueid=venueid,
        primary_area=primary_area,
        keywords=keywords,
    )
    return result == "created"


def upsert_paper(
    paper_id: str,
    title: str,
    authors: list[str],
    abstract: str,
    venue: str,
    venueid: str,
    primary_area: str | None = None,
    keywords: list[str] | None = None,
) -> str:
    """Upsert paper metadata.

    Returns one of:
    - "created": paper was inserted.
    - "updated": paper existed and fields changed.
    - "skipped": paper existed and fields were unchanged.
    """
    encoded_authors = json.dumps(authors)
    normalized_keywords = keywords or []
    encoded_keywords = json.dumps(normalized_keywords) if normalized_keywords else None

    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT title, authors, abstract, venue, venueid, primary_area, keywords
            FROM papers
            WHERE id = ?
            """,
            (paper_id,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO papers (
                    id, title, authors, abstract, venue, venueid, primary_area, keywords
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    title,
                    encoded_authors,
                    abstract,
                    venue,
                    venueid,
                    primary_area,
                    encoded_keywords,
                ),
            )
            _sync_paper_relations(conn, paper_id, authors, normalized_keywords)
            return "created"

        unchanged = (
            existing["title"] == title
            and existing["authors"] == encoded_authors
            and existing["abstract"] == abstract
            and existing["venue"] == venue
            and existing["venueid"] == venueid
            and existing["primary_area"] == primary_area
            and existing["keywords"] == encoded_keywords
        )
        if unchanged:
            _sync_paper_relations(conn, paper_id, authors, normalized_keywords)
            return "skipped"

        conn.execute(
            """
            UPDATE papers
            SET title = ?, authors = ?, abstract = ?, venue = ?, venueid = ?,
                primary_area = ?, keywords = ?
            WHERE id = ?
            """,
            (
                title,
                encoded_authors,
                abstract,
                venue,
                venueid,
                primary_area,
                encoded_keywords,
                paper_id,
            ),
        )
        _sync_paper_relations(conn, paper_id, authors, normalized_keywords)
        return "updated"


def get_paper(paper_id: str) -> dict | None:
    """Get a paper by ID."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if row is None:
            return None
        paper = dict(row)
        paper["authors"] = json.loads(paper["authors"]) if paper["authors"] else []
        paper["keywords"] = json.loads(paper["keywords"]) if paper["keywords"] else []
        return paper


def update_pdf_path(paper_id: str, pdf_path: str) -> None:
    """Update the PDF path for a paper."""
    with get_connection() as conn:
        conn.execute("UPDATE papers SET pdf_path = ? WHERE id = ?", (pdf_path, paper_id))


def update_pdf_metadata(
    paper_id: str,
    pdf_path: str,
    pdf_sha256: str,
    pdf_size_bytes: int,
) -> None:
    """Update PDF path plus integrity metadata for a paper."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE papers
            SET pdf_path = ?, pdf_sha256 = ?, pdf_size_bytes = ?
            WHERE id = ?
            """,
            (pdf_path, pdf_sha256, pdf_size_bytes, paper_id),
        )


def list_papers(
    venue: str | None = None,
    tag: str | None = None,
    author: str | None = None,
    keyword: str | None = None,
    downloaded_only: bool = False,
) -> list[dict]:
    """List papers with optional filters."""
    with get_connection() as conn:
        query = "SELECT DISTINCT p.* FROM papers p"
        conditions = []
        params: list[str] = []

        if tag:
            query += " JOIN paper_tags pt ON p.id = pt.paper_id JOIN tags t ON pt.tag_id = t.id"
            conditions.append("t.name = ?")
            params.append(tag)

        if author:
            query += (
                " JOIN paper_authors pa ON p.id = pa.paper_id JOIN authors a ON pa.author_id = a.id"
            )
            conditions.append("a.name LIKE ?")
            params.append(f"%{author}%")

        if keyword:
            query += (
                " JOIN paper_keywords pk ON p.id = pk.paper_id "
                "JOIN keyword_terms kt ON pk.keyword_id = kt.id"
            )
            conditions.append("kt.term LIKE ?")
            params.append(f"%{keyword}%")

        if venue:
            conditions.append("p.venue LIKE ?")
            params.append(f"%{venue}%")

        if downloaded_only:
            conditions.append("p.pdf_path IS NOT NULL")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY p.created_at DESC"

        rows = conn.execute(query, params).fetchall()
        papers = []
        for row in rows:
            paper = dict(row)
            paper["authors"] = json.loads(paper["authors"]) if paper["authors"] else []
            paper["keywords"] = json.loads(paper["keywords"]) if paper["keywords"] else []
            papers.append(paper)
        return papers


def _library_paper_from_row(row: sqlite3.Row) -> dict:
    state = _paper_reconcile_state(row)
    has_pdf = bool(row["pdf_path"])
    tags = [tag for tag in str(row["tag_names"] or "").split(",") if tag]
    return {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "abstract": str(row["abstract"] or ""),
        "authors": json.loads(row["authors"]) if row["authors"] else [],
        "keywords": json.loads(row["keywords"]) if row["keywords"] else [],
        "tags": tags,
        "venue": str(row["venue"] or ""),
        "venueid": str(row["venueid"] or ""),
        "primary_area": str(row["primary_area"] or ""),
        "created_at": row["created_at"],
        "pdf_path": str(row["pdf_path"]) if row["pdf_path"] else None,
        "has_pdf": has_pdf,
        "pdf_ready": has_pdf and not state["missing_file"],
        "file_exists": has_pdf and not state["missing_file"],
        "missing_record": state["missing_record"],
        "missing_file": state["missing_file"],
        "metadata_missing": state["metadata_missing"],
        "latest_download_status": row["latest_download_status"],
        "latest_download_error": row["latest_download_error"],
    }


def search_papers(
    query: str = "",
    limit: int = 20,
    *,
    downloaded_only: bool = False,
) -> list[dict]:
    """Search papers across core metadata plus normalized tag/author/keyword relations."""
    normalized_query = query.strip()
    bounded_limit = max(1, min(limit, 100))
    with get_connection() as conn:
        sql = """
            SELECT
                p.id,
                p.title,
                p.abstract,
                p.authors,
                p.keywords,
                p.venue,
                p.venueid,
                p.primary_area,
                p.pdf_path,
                p.pdf_sha256,
                p.pdf_size_bytes,
                p.created_at,
                COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tag_names,
                (
                    SELECT dj.status
                    FROM download_jobs dj
                    WHERE dj.paper_id = p.id
                    ORDER BY dj.id DESC
                    LIMIT 1
                ) AS latest_download_status,
                (
                    SELECT dj.last_error
                    FROM download_jobs dj
                    WHERE dj.paper_id = p.id
                    ORDER BY dj.id DESC
                    LIMIT 1
                ) AS latest_download_error
            FROM papers p
            LEFT JOIN paper_tags pt ON p.id = pt.paper_id
            LEFT JOIN tags t ON pt.tag_id = t.id
            WHERE 1 = 1
        """
        params: list[str | int] = []
        if downloaded_only:
            sql += " AND p.pdf_path IS NOT NULL"

        if normalized_query:
            needle = f"%{normalized_query}%"
            sql += """
              AND (
                    p.id LIKE ?
                 OR p.title LIKE ?
                 OR COALESCE(p.abstract, '') LIKE ?
                 OR COALESCE(p.venue, '') LIKE ?
                 OR COALESCE(p.venueid, '') LIKE ?
                 OR COALESCE(p.primary_area, '') LIKE ?
                 OR COALESCE(p.authors, '') LIKE ?
                 OR COALESCE(p.keywords, '') LIKE ?
                 OR EXISTS (
                        SELECT 1
                        FROM paper_authors pa
                        JOIN authors a ON a.id = pa.author_id
                        WHERE pa.paper_id = p.id AND a.name LIKE ?
                    )
                 OR EXISTS (
                        SELECT 1
                        FROM paper_keywords pk
                        JOIN keyword_terms kt ON kt.id = pk.keyword_id
                        WHERE pk.paper_id = p.id AND kt.term LIKE ?
                    )
                 OR EXISTS (
                        SELECT 1
                        FROM paper_tags pt2
                        JOIN tags t2 ON t2.id = pt2.tag_id
                        WHERE pt2.paper_id = p.id AND t2.name LIKE ?
                    )
              )
            """
            params.extend([needle] * 11)

        sql += """
            GROUP BY p.id
            ORDER BY
                CASE
                    WHEN ? != '' AND LOWER(p.id) = LOWER(?) THEN 0
                    WHEN ? != '' AND LOWER(p.title) = LOWER(?) THEN 1
                    WHEN p.pdf_path IS NOT NULL THEN 2
                    ELSE 3
                END,
                p.created_at DESC,
                p.id DESC
            LIMIT ?
        """
        params.extend(
            [
                normalized_query,
                normalized_query,
                normalized_query,
                normalized_query,
                bounded_limit,
            ]
        )
        rows = conn.execute(sql, params).fetchall()

    return [_library_paper_from_row(row) for row in rows]


def get_library_paper(paper_id: str) -> dict | None:
    """Return a rich paper record for UI/detail views."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                p.id,
                p.title,
                p.abstract,
                p.authors,
                p.keywords,
                p.venue,
                p.venueid,
                p.primary_area,
                p.pdf_path,
                p.pdf_sha256,
                p.pdf_size_bytes,
                p.created_at,
                COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tag_names,
                (
                    SELECT dj.status
                    FROM download_jobs dj
                    WHERE dj.paper_id = p.id
                    ORDER BY dj.id DESC
                    LIMIT 1
                ) AS latest_download_status,
                (
                    SELECT dj.last_error
                    FROM download_jobs dj
                    WHERE dj.paper_id = p.id
                    ORDER BY dj.id DESC
                    LIMIT 1
                ) AS latest_download_error
            FROM papers p
            LEFT JOIN paper_tags pt ON p.id = pt.paper_id
            LEFT JOIN tags t ON pt.tag_id = t.id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (paper_id,),
        ).fetchone()

    if row is None:
        return None
    return _library_paper_from_row(row)


def search_downloaded_papers(query: str = "", limit: int = 20) -> list[dict]:
    """Search downloaded papers by identifier and core metadata fields."""
    return search_papers(query=query, limit=limit, downloaded_only=True)


def get_downloaded_paper_file(paper_id: str) -> dict | None:
    """Get downloaded paper file metadata by paper ID."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, title, pdf_path, pdf_sha256, pdf_size_bytes
            FROM papers
            WHERE id = ? AND pdf_path IS NOT NULL
            """,
            (paper_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def replace_paper_forum_cache(
    paper_id: str,
    reviews: list[dict[str, object | None]],
    posts: list[dict[str, object | None]],
) -> None:
    """Replace cached forum data for one paper atomically."""
    with get_connection() as conn:
        conn.execute("DELETE FROM paper_reviews WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM discussion_posts WHERE paper_id = ?", (paper_id,))

        if reviews:
            conn.executemany(
                """
                INSERT INTO paper_reviews (
                    id,
                    paper_id,
                    reviewer,
                    rating,
                    confidence,
                    summary,
                    strengths,
                    weaknesses,
                    questions,
                    limitations,
                    soundness,
                    presentation,
                    contribution,
                    recommendation,
                    full_text,
                    created_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        review["id"],
                        paper_id,
                        review["reviewer"],
                        review["rating"],
                        review["confidence"],
                        review["summary"],
                        review["strengths"],
                        review["weaknesses"],
                        review["questions"],
                        review["limitations"],
                        review["soundness"],
                        review["presentation"],
                        review["contribution"],
                        review["recommendation"],
                        review["full_text"],
                        review["created_at_ms"],
                    )
                    for review in reviews
                ],
            )

        if posts:
            conn.executemany(
                """
                INSERT INTO discussion_posts (
                    id,
                    paper_id,
                    reply_to,
                    author,
                    content,
                    post_type,
                    title,
                    created_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        post["id"],
                        paper_id,
                        post["reply_to"],
                        post["author"],
                        post["content"],
                        post["post_type"],
                        post["title"],
                        post["created_at_ms"],
                    )
                    for post in posts
                ],
            )

        conn.execute(
            """
            INSERT INTO paper_forum_cache (paper_id, review_count, post_count, cached_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(paper_id) DO UPDATE SET
                review_count = excluded.review_count,
                post_count = excluded.post_count,
                cached_at = CURRENT_TIMESTAMP
            """,
            (paper_id, len(reviews), len(posts)),
        )


def get_paper_forum_cache(paper_id: str) -> dict | None:
    """Return cache metadata for one paper, if forum data was cached."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT paper_id, review_count, post_count, cached_at
            FROM paper_forum_cache
            WHERE paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "paper_id": str(row["paper_id"]),
        "review_count": int(row["review_count"]),
        "post_count": int(row["post_count"]),
        "cached_at": row["cached_at"],
    }


def get_cached_paper_reviews(paper_id: str) -> list[dict]:
    """Return cached structured reviews for one paper."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                paper_id,
                reviewer,
                rating,
                confidence,
                summary,
                strengths,
                weaknesses,
                questions,
                limitations,
                soundness,
                presentation,
                contribution,
                recommendation,
                full_text,
                created_at_ms
            FROM paper_reviews
            WHERE paper_id = ?
            ORDER BY COALESCE(created_at_ms, 0), id
            """,
            (paper_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_cached_discussion_posts(paper_id: str) -> list[dict]:
    """Return cached discussion posts for one paper."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                paper_id,
                reply_to,
                author,
                content,
                post_type,
                title,
                created_at_ms
            FROM discussion_posts
            WHERE paper_id = ?
            ORDER BY COALESCE(created_at_ms, 0), id
            """,
            (paper_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_tag(paper_id: str, tag_name: str) -> None:
    """Add a tag to a paper."""
    with get_connection() as conn:
        # Insert tag if it doesn't exist
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()[0]
        # Link paper to tag
        conn.execute(
            "INSERT OR IGNORE INTO paper_tags (paper_id, tag_id) VALUES (?, ?)",
            (paper_id, tag_id),
        )


def get_paper_tags(paper_id: str) -> list[str]:
    """Get all tags for a paper."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.name FROM tags t
            JOIN paper_tags pt ON t.id = pt.tag_id
            WHERE pt.paper_id = ?
            """,
            (paper_id,),
        ).fetchall()
        return [row[0] for row in rows]


def add_note(paper_id: str, content: str) -> None:
    """Add a note to a paper."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO notes (paper_id, content) VALUES (?, ?)",
            (paper_id, content),
        )


def get_paper_notes(paper_id: str) -> list[dict]:
    """Get all notes for a paper."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT content, created_at FROM notes WHERE paper_id = ? ORDER BY created_at",
            (paper_id,),
        ).fetchall()
        return [{"content": row[0], "created_at": row[1]} for row in rows]


def _status_counts(conn: sqlite3.Connection, table_name: str) -> dict[str, int]:
    counts = {status: 0 for status in JOB_STATUSES}
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS count FROM {table_name} GROUP BY status"
    ).fetchall()
    for row in rows:
        counts[row["status"]] = int(row["count"])
    return counts


def _paper_reconcile_state(row: sqlite3.Row) -> dict[str, bool]:
    pdf_path = row["pdf_path"]
    has_pdf_record = bool(pdf_path)
    missing_record = not has_pdf_record
    missing_file = has_pdf_record and not Path(str(pdf_path)).exists()
    metadata_missing = has_pdf_record and (
        row["pdf_sha256"] is None or row["pdf_size_bytes"] is None
    )
    return {
        "needs_reconcile": missing_record or missing_file or metadata_missing,
        "missing_record": missing_record,
        "missing_file": missing_file,
        "metadata_missing": metadata_missing,
    }


def _normalize_download_priority_text(*values: object | None) -> str:
    chunks: list[str] = []
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        normalized = " ".join(
            raw.replace("_", " ").replace("-", " ").replace("/", " ").split()
        ).lower()
        if normalized:
            chunks.append(normalized)
    return " ".join(chunks)


def _download_priority_from_metadata(venue: object | None, venueid: object | None) -> int:
    normalized = _normalize_download_priority_text(venue, venueid)
    if "oral" in normalized:
        return DOWNLOAD_PRIORITY_ORAL
    if "spotlight" in normalized:
        return DOWNLOAD_PRIORITY_SPOTLIGHT
    if "poster" in normalized:
        return DOWNLOAD_PRIORITY_POSTER
    return DOWNLOAD_PRIORITY_DEFAULT


def _download_priority_for_paper_id(conn: sqlite3.Connection, paper_id: str) -> int:
    row = conn.execute(
        "SELECT venue, venueid FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if row is None:
        return DOWNLOAD_PRIORITY_DEFAULT
    return _download_priority_from_metadata(row["venue"], row["venueid"])


def _download_sort_key(row: sqlite3.Row) -> tuple[int, str, str]:
    return (
        _download_priority_from_metadata(row["venue"], row["venueid"]),
        str(row["created_at"] or ""),
        str(row["id"]),
    )


def _download_reconcile_candidates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT id, pdf_path, pdf_sha256, pdf_size_bytes, venue, venueid, created_at
        FROM papers
        """
    ).fetchall()
    candidates: list[sqlite3.Row] = []
    for row in rows:
        if _paper_reconcile_state(row)["needs_reconcile"]:
            candidates.append(row)
    candidates.sort(key=_download_sort_key)
    return [str(row["id"]) for row in candidates]


def list_papers_needing_reconcile(limit: int = 20) -> list[dict]:
    """List papers whose local PDF record needs download or reconciliation work."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, title, venue, venueid, pdf_path, pdf_sha256, pdf_size_bytes, created_at
            FROM papers
            """
        ).fetchall()

    pending_rows: list[tuple[tuple[int, str, str], dict[str, object]]] = []
    for row in rows:
        state = _paper_reconcile_state(row)
        if not state["needs_reconcile"]:
            continue

        reasons: list[str] = []
        if state["missing_record"]:
            reasons.append("missing-record")
        if state["missing_file"]:
            reasons.append("missing-file")
        if state["metadata_missing"]:
            reasons.append("missing-metadata")

        pending_rows.append(
            (
                _download_sort_key(row),
                {
                    "id": str(row["id"]),
                    "title": str(row["title"]),
                    "venue": str(row["venue"] or ""),
                    "pdf_path": str(row["pdf_path"]) if row["pdf_path"] else None,
                    "created_at": row["created_at"],
                    "missing_record": state["missing_record"],
                    "missing_file": state["missing_file"],
                    "metadata_missing": state["metadata_missing"],
                    "reasons": reasons,
                },
            )
        )

    pending_rows.sort(key=lambda item: item[0])
    return [paper for _, paper in pending_rows[:limit]]


def get_db_stats() -> dict:
    """Return high-level database and queue inventory counts."""
    with get_connection() as conn:
        paper_counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN pdf_path IS NOT NULL THEN 1 ELSE 0 END) AS recorded,
                SUM(CASE WHEN pdf_path IS NULL THEN 1 ELSE 0 END) AS missing_record
            FROM papers
            """
        ).fetchone()
        tag_count = conn.execute("SELECT COUNT(*) AS count FROM tags").fetchone()["count"]
        note_count = conn.execute("SELECT COUNT(*) AS count FROM notes").fetchone()["count"]
        author_count = conn.execute("SELECT COUNT(*) AS count FROM authors").fetchone()["count"]
        keyword_count = conn.execute("SELECT COUNT(*) AS count FROM keyword_terms").fetchone()[
            "count"
        ]
        paper_rows = conn.execute(
            "SELECT pdf_path, pdf_sha256, pdf_size_bytes FROM papers"
        ).fetchall()
        sync_counts = _status_counts(conn, "sync_jobs")
        download_counts = _status_counts(conn, "download_jobs")

    reconcile_count = 0
    missing_file_count = 0
    for row in paper_rows:
        state = _paper_reconcile_state(row)
        if state["missing_file"]:
            missing_file_count += 1
        if state["needs_reconcile"]:
            reconcile_count += 1

    return {
        "papers": {
            "total": int(paper_counts["total"] or 0),
            "downloaded_recorded": int(paper_counts["recorded"] or 0),
            "missing_record": int(paper_counts["missing_record"] or 0),
            "needs_reconcile": reconcile_count,
            "missing_files": missing_file_count,
        },
        "relations": {
            "authors": int(author_count),
            "keywords": int(keyword_count),
            "tags": int(tag_count),
            "notes": int(note_count),
        },
        "sync_jobs": sync_counts,
        "download_jobs": download_counts,
    }


def enqueue_sync_job(conference: str, year: int, decision: str) -> tuple[int, bool]:
    """Enqueue a pending background sync job.

    Returns `(job_id, created)` where `created` indicates whether a new job was created.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO sync_jobs (conference, year, decision, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (conference, year, decision),
        )
        if cursor.rowcount:
            job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(job_id), True

        row = conn.execute(
            """
            SELECT id
            FROM sync_jobs
            WHERE conference = ? AND year = ? AND decision = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (conference, year, decision),
        ).fetchone()
        return int(row[0]), False


def claim_next_sync_job() -> dict | None:
    """Claim the oldest pending sync job for worker execution."""
    with get_connection() as conn:
        _begin_immediate(conn)
        row = conn.execute(
            """
            SELECT *
            FROM sync_jobs
            WHERE status = 'pending'
            ORDER BY created_at, id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None

        cursor = conn.execute(
            """
            UPDATE sync_jobs
            SET status = 'running',
                attempts = attempts + 1,
                started_at = CURRENT_TIMESTAMP,
                last_error = NULL
            WHERE id = ? AND status = 'pending'
            """,
            (row["id"],),
        )
        if cursor.rowcount == 0:
            return None

        claimed = conn.execute("SELECT * FROM sync_jobs WHERE id = ?", (row["id"],)).fetchone()
        return dict(claimed)


def complete_sync_job(job_id: int) -> None:
    """Mark a running sync job as completed."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE sync_jobs
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )


def fail_sync_job(job_id: int, error_message: str) -> None:
    """Mark a running sync job as failed with error details."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE sync_jobs
            SET status = 'failed',
                last_error = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error_message, job_id),
        )


def get_sync_job(job_id: int) -> dict | None:
    """Fetch a sync job by ID."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sync_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None


def _enqueue_or_refresh_download_job(
    conn: sqlite3.Connection,
    paper_id: str,
) -> tuple[int, bool]:
    priority = _download_priority_for_paper_id(conn, paper_id)
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO download_jobs (paper_id, status, download_priority)
        VALUES (?, 'pending', ?)
        """,
        (paper_id, priority),
    )
    if cursor.rowcount:
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return int(job_id), True

    row = conn.execute(
        """
        SELECT id, download_priority
        FROM download_jobs
        WHERE paper_id = ? AND status IN ('pending', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"active download job missing for paper: {paper_id}")

    if int(row["download_priority"]) != priority:
        conn.execute(
            "UPDATE download_jobs SET download_priority = ? WHERE id = ?",
            (priority, row["id"]),
        )
    return int(row["id"]), False


def enqueue_download_job(paper_id: str) -> tuple[int, bool]:
    """Enqueue a pending background download job for a paper."""
    with get_connection() as conn:
        return _enqueue_or_refresh_download_job(conn, paper_id)


def enqueue_reconcile_download_jobs(limit: int | None = None) -> dict:
    """Queue papers whose download record needs download or reconciliation work."""
    with get_connection() as conn:
        candidates = _download_reconcile_candidates(conn)
        if limit is not None:
            candidates = candidates[:limit]

        created = 0
        queued_job_ids: list[int] = []
        for paper_id in candidates:
            job_id, was_created = _enqueue_or_refresh_download_job(conn, paper_id)
            if was_created:
                created += 1
                queued_job_ids.append(job_id)

    return {
        "candidates": len(candidates),
        "created": created,
        "skipped": len(candidates) - created,
        "job_ids": queued_job_ids,
    }


def claim_next_download_job(worker_id: str, lease_seconds: int) -> dict | None:
    """Claim the oldest pending or expired download job for worker execution."""
    lease_modifier = f"+{lease_seconds} seconds"
    with get_connection() as conn:
        _begin_immediate(conn)
        row = conn.execute(
            """
            SELECT *
            FROM download_jobs
            WHERE status = 'pending'
               OR (
                    status = 'running'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= CURRENT_TIMESTAMP
               )
            ORDER BY download_priority, created_at, id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None

        cursor = conn.execute(
            """
            UPDATE download_jobs
            SET status = 'running',
                attempts = attempts + 1,
                claimed_by = ?,
                lease_expires_at = datetime('now', ?),
                started_at = CURRENT_TIMESTAMP,
                completed_at = NULL,
                last_error = NULL
            WHERE id = ?
              AND (
                    status = 'pending'
                OR (
                        status = 'running'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= CURRENT_TIMESTAMP
                )
              )
            """,
            (worker_id, lease_modifier, row["id"]),
        )
        if cursor.rowcount == 0:
            return None

        claimed = conn.execute("SELECT * FROM download_jobs WHERE id = ?", (row["id"],)).fetchone()
        return dict(claimed)


def complete_download_job(job_id: int) -> None:
    """Mark a running download job as completed."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE download_jobs
            SET status = 'completed',
                claimed_by = NULL,
                lease_expires_at = NULL,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )


def fail_download_job(job_id: int, error_message: str) -> None:
    """Mark a running download job as failed with error details."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE download_jobs
            SET status = 'failed',
                claimed_by = NULL,
                lease_expires_at = NULL,
                last_error = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error_message, job_id),
        )


def get_download_job(job_id: int) -> dict | None:
    """Fetch a download job by ID."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM download_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None


def list_download_jobs(limit: int = 20, status: str | None = None) -> list[dict]:
    """List recent download jobs, optionally filtered by status."""
    with get_connection() as conn:
        query = (
            "SELECT "
            "download_jobs.*, "
            "papers.title AS paper_title, "
            "papers.venue AS paper_venue "
            "FROM download_jobs "
            "LEFT JOIN papers ON papers.id = download_jobs.paper_id "
            "WHERE (? IS NULL OR download_jobs.status = ?) "
            "ORDER BY "
            "CASE download_jobs.status "
            "WHEN 'running' THEN 0 "
            "WHEN 'pending' THEN 1 "
            "WHEN 'failed' THEN 2 "
            "ELSE 3 END, "
            "COALESCE(download_jobs.started_at, download_jobs.created_at) DESC, download_jobs.id DESC "
            "LIMIT ?"
        )
        rows = conn.execute(query, (status, status, limit)).fetchall()
        return [dict(row) for row in rows]


def get_unresolved_failed_download_jobs(limit: int = 20) -> dict:
    """Return failed download jobs whose papers still need reconciliation."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                download_jobs.*,
                papers.pdf_path,
                papers.pdf_sha256,
                papers.pdf_size_bytes
            FROM download_jobs
            JOIN papers ON papers.id = download_jobs.paper_id
            WHERE download_jobs.status = 'failed'
            ORDER BY COALESCE(
                download_jobs.completed_at,
                download_jobs.started_at,
                download_jobs.created_at
            ) DESC,
            download_jobs.id DESC
            """
        ).fetchall()

    unresolved_jobs: list[dict] = []
    unresolved_count = 0
    for row in rows:
        if not _paper_reconcile_state(row)["needs_reconcile"]:
            continue
        unresolved_count += 1
        if len(unresolved_jobs) < limit:
            unresolved_jobs.append(dict(row))

    return {"count": unresolved_count, "jobs": unresolved_jobs}


def get_download_queue_status(limit: int = 20) -> dict:
    """Return aggregate download queue counts plus recent jobs."""
    with get_connection() as conn:
        counts = _status_counts(conn, "download_jobs")
    return {"counts": counts, "jobs": list_download_jobs(limit=limit)}


def count_claimable_download_jobs() -> int:
    """Count download jobs that a local worker could claim right now."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN status = 'pending' THEN 1
                        WHEN status = 'running'
                         AND lease_expires_at IS NOT NULL
                         AND lease_expires_at <= CURRENT_TIMESTAMP THEN 1
                        ELSE 0
                    END
                ) AS claimable
            FROM download_jobs
            """
        ).fetchone()
    return int(row["claimable"] or 0)
