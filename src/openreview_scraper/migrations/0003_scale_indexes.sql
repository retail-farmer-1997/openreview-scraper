-- Normalize high-cardinality author/keyword relationships
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id),
    author_order INTEGER NOT NULL,
    PRIMARY KEY (paper_id, author_id)
);

CREATE TABLE IF NOT EXISTS keyword_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_keywords (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    keyword_id INTEGER NOT NULL REFERENCES keyword_terms(id),
    PRIMARY KEY (paper_id, keyword_id)
);

CREATE INDEX IF NOT EXISTS idx_authors_name ON authors(name);
CREATE INDEX IF NOT EXISTS idx_keyword_terms_term ON keyword_terms(term);
CREATE INDEX IF NOT EXISTS idx_paper_authors_author_id ON paper_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_paper_keywords_keyword_id ON paper_keywords(keyword_id);
