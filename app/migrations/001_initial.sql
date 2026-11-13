CREATE TABLE accounts (
 id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL DEFAULT '技术资讯',
 topics TEXT NOT NULL DEFAULT '软件开发、AI、开发工具、云与基础设施',
 audience TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '简洁、准确、有来源',
 timezone TEXT NOT NULL DEFAULT '', daily_limit INTEGER NOT NULL DEFAULT 0,
 generation_limit INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 1
);
INSERT INTO accounts(id) VALUES(1);
CREATE TABLE sources (
 id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 body TEXT NOT NULL, published_at TEXT, retrieved_at TEXT NOT NULL,
 confirmed INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE drafts (
 id INTEGER PRIMARY KEY, source_ids TEXT NOT NULL, language TEXT NOT NULL,
 column_name TEXT NOT NULL, latest_version INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'review_pending', last_error TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE draft_versions (
 id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES drafts(id),
 version_no INTEGER NOT NULL, segments TEXT NOT NULL, source_refs TEXT NOT NULL,
 content_hash TEXT NOT NULL, model TEXT NOT NULL DEFAULT 'manual',
 editor_notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(draft_id, version_no)
);
CREATE TABLE approvals (
 version_id INTEGER PRIMARY KEY REFERENCES draft_versions(id), content_hash TEXT NOT NULL,
 actor TEXT NOT NULL, approved_at TEXT NOT NULL
);
CREATE TABLE publications (
 id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL UNIQUE REFERENCES draft_versions(id),
 target TEXT NOT NULL, scheduled_at TEXT NOT NULL, status TEXT NOT NULL,
 remote_id TEXT, release_url TEXT, platform_post_id TEXT,
 cancel_requested INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE jobs (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
 dedupe_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, run_after REAL NOT NULL,
 lease_owner TEXT, lease_until REAL, last_error TEXT
);
CREATE TABLE events (
 id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
 event_type TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'system',
 details TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE usage_records (
 id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES drafts(id),
 day TEXT NOT NULL, status TEXT NOT NULL, usage_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL
);
CREATE INDEX jobs_due ON jobs(status, run_after, lease_until);
CREATE INDEX publications_time ON publications(scheduled_at, status);
