-- Background sync job queue for worker execution path
CREATE TABLE IF NOT EXISTS sync_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conference TEXT NOT NULL,
    year INTEGER NOT NULL,
    decision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sync_jobs_status_created_at
    ON sync_jobs(status, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_jobs_unique_request_status
    ON sync_jobs(conference, year, decision, status);
