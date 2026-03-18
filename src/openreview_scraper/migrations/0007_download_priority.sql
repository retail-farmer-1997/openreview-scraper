-- Prioritize download jobs by decision bucket before FIFO claim order
ALTER TABLE download_jobs
    ADD COLUMN download_priority INTEGER NOT NULL DEFAULT 3;

UPDATE download_jobs
SET download_priority = COALESCE(
    (
        SELECT CASE
            WHEN instr(
                lower(COALESCE(papers.venue, '') || ' ' || COALESCE(papers.venueid, '')),
                'oral'
            ) > 0 THEN 0
            WHEN instr(
                lower(COALESCE(papers.venue, '') || ' ' || COALESCE(papers.venueid, '')),
                'spotlight'
            ) > 0 THEN 1
            WHEN instr(
                lower(COALESCE(papers.venue, '') || ' ' || COALESCE(papers.venueid, '')),
                'poster'
            ) > 0 THEN 2
            ELSE 3
        END
        FROM papers
        WHERE papers.id = download_jobs.paper_id
    ),
    3
);

CREATE INDEX IF NOT EXISTS idx_download_jobs_status_priority_created_at
    ON download_jobs(status, download_priority, created_at);
