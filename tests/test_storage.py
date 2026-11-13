from pathlib import Path
import sqlite3
from app.db import Database


def test_migration_preserves_existing_publication_and_backups(tmp_path):
    database = Database(tmp_path)
    sql = (Path(__file__).parents[1] / 'app/migrations/001_initial.sql').read_text()
    with sqlite3.connect(database.path) as conn:
        conn.executescript(sql)
        conn.execute("UPDATE accounts SET name='Existing account'")
        conn.execute('PRAGMA user_version=1')
    database.initialize()
    assert database.one('SELECT name FROM accounts')['name'] == 'Existing account'
    assert len(list((tmp_path / 'backups').glob('*.sqlite3'))) == 1
