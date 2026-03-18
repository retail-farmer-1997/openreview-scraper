-- Track PDF integrity metadata for storage hardening
ALTER TABLE papers ADD COLUMN pdf_sha256 TEXT;
ALTER TABLE papers ADD COLUMN pdf_size_bytes INTEGER;
