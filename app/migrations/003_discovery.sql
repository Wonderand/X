CREATE TABLE discovery_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), daily_limit INTEGER NOT NULL DEFAULT 0
);
INSERT INTO discovery_settings(id) VALUES(1);
CREATE TABLE search_rules (
 id INTEGER PRIMARY KEY, query TEXT NOT NULL, backend TEXT NOT NULL,
 days INTEGER NOT NULL, result_limit INTEGER NOT NULL,
 interval_minutes INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
 next_run_at REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE search_runs (
 id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL REFERENCES search_rules(id),
 request_json TEXT NOT NULL, backend TEXT NOT NULL, status TEXT NOT NULL,
 coverage_json TEXT NOT NULL DEFAULT '[]', errors_json TEXT NOT NULL DEFAULT '[]',
 limitations_json TEXT NOT NULL DEFAULT '[]', candidate_count INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE UNIQUE INDEX search_active_rule ON search_runs(rule_id) WHERE status IN ('queued','running');
CREATE TABLE discovery_topics (
 id INTEGER PRIMARY KEY, group_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE discovery_candidates (
 id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES discovery_topics(id),
 canonical_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL, snippet TEXT NOT NULL,
 backend TEXT NOT NULL, platform TEXT NOT NULL, published_at TEXT,
 score REAL NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
 source_id INTEGER REFERENCES sources(id), first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE candidate_hits (
 run_id INTEGER NOT NULL REFERENCES search_runs(id),
 candidate_id INTEGER NOT NULL REFERENCES discovery_candidates(id),
 rank INTEGER NOT NULL, raw_json TEXT NOT NULL,
 PRIMARY KEY(run_id,candidate_id)
);
CREATE TABLE discovery_calls (
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
 reference TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX discovery_calls_day ON discovery_calls(day);
CREATE TABLE candidate_reads (
 id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES discovery_candidates(id),
 hit_run_id INTEGER NOT NULL REFERENCES search_runs(id), status TEXT NOT NULL,
 body TEXT, last_error TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE UNIQUE INDEX candidate_read_active ON candidate_reads(candidate_id) WHERE status IN ('queued','running');
