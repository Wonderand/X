ALTER TABLE jobs ADD COLUMN failure_streak INTEGER NOT NULL DEFAULT 0;
ALTER TABLE publications RENAME TO publications_previous;
CREATE TABLE publications (
 id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES draft_versions(id),
 target TEXT NOT NULL, scheduled_at TEXT NOT NULL, status TEXT NOT NULL,
 remote_id TEXT, release_url TEXT, platform_post_id TEXT,
 cancel_requested INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(version_id,target,scheduled_at)
);
INSERT INTO publications SELECT * FROM publications_previous;
DROP TABLE publications_previous;
CREATE UNIQUE INDEX publication_active_version ON publications(version_id) WHERE status!='cancelled';
CREATE INDEX publications_time ON publications(scheduled_at,status);
