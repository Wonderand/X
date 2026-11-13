from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def event(conn, entity, entity_id, action, actor='system', **details):
    conn.execute('INSERT INTO events(entity_type,entity_id,event_type,actor,details,created_at) VALUES(?,?,?,?,?,?)',
                 (entity, entity_id, action, actor, json.dumps(details, ensure_ascii=False), now()))


class Database:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / 'xops.sqlite3'

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')
        return connection

    def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            migrations = sorted((Path(__file__).parent / 'migrations').glob('*.sql'))
            existing_version = connection.execute('PRAGMA user_version').fetchone()[0]
            if 0 < existing_version < len(migrations):
                backup_dir = self.data_dir / 'backups'
                backup_dir.mkdir(exist_ok=True)
                backup_path = backup_dir / f'before-migration-{existing_version}-{uuid.uuid4().hex}.sqlite3'
                with closing(sqlite3.connect(backup_path)) as backup:
                    connection.backup(backup)
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if version > len(migrations):
                    raise RuntimeError('Unsupported database schema')
                for migration in migrations[version:]:
                    sql = migration.read_text(encoding='utf-8')
                    for statement in sql.split(';'):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute(f'PRAGMA user_version={int(migration.name.split("_")[0])}')
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rows(self, sql, args=()):
        connection = self.connect()
        try:
            return [dict(row) for row in connection.execute(sql, args).fetchall()]
        finally:
            connection.close()

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None
