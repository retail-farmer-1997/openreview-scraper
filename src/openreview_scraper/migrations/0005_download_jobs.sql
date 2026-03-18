-- Background download job queue with lease-based worker claims
CREATE TABLE IF NOT EXISTS download_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    lease_expires_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_download_jobs_status_created_at
    ON download_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_download_jobs_paper_id_created_at
    ON download_jobs(paper_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_download_jobs_unique_active_paper
    ON download_jobs(paper_id)
    WHERE status IN ('pending', 'running');
