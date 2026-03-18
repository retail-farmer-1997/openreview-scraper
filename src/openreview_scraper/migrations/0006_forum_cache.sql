-- Structured cache for OpenReview reviews and discussion posts
CREATE TABLE IF NOT EXISTS paper_forum_cache (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id),
    review_count INTEGER NOT NULL DEFAULT 0,
    post_count INTEGER NOT NULL DEFAULT 0,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_reviews (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    reviewer TEXT NOT NULL,
    rating TEXT,
    confidence TEXT,
    summary TEXT,
    strengths TEXT,
    weaknesses TEXT,
    questions TEXT,
    limitations TEXT,
    soundness TEXT,
    presentation TEXT,
    contribution TEXT,
    recommendation TEXT,
    full_text TEXT,
    created_at_ms INTEGER
);

CREATE TABLE IF NOT EXISTS discussion_posts (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    reply_to TEXT,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    post_type TEXT NOT NULL,
    title TEXT,
    created_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_paper_reviews_paper_id_created_at
    ON paper_reviews(paper_id, created_at_ms, id);

CREATE INDEX IF NOT EXISTS idx_discussion_posts_paper_id_created_at
    ON discussion_posts(paper_id, created_at_ms, id);
