-- Baseline schema migration
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT,
    abstract TEXT,
    venue TEXT,
    venueid TEXT,
    primary_area TEXT,
    keywords TEXT,
    pdf_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id TEXT REFERENCES papers(id),
    tag_id INTEGER REFERENCES tags(id),
    PRIMARY KEY (paper_id, tag_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT REFERENCES papers(id),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_papers_venue ON papers(venue);
CREATE INDEX IF NOT EXISTS idx_papers_venueid ON papers(venueid);
