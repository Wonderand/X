import pytest

from app.config import Settings
from app.db import Database
from app.drafts import Editorial


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, session_secret='x' * 48)


@pytest.fixture
def db(settings):
    database = Database(settings.data_dir)
    database.initialize()
    with database.transaction() as conn:
        conn.execute("UPDATE accounts SET timezone='Asia/Shanghai',daily_limit=3,generation_limit=5,paused=0 WHERE id=1")
    return database


@pytest.fixture
def editorial(db):
    return Editorial(db)


@pytest.fixture
def approved(editorial):
    source = editorial.add_source('https://example.com/release', 'Release', 'Official release notes')
    draft = editorial.create_draft([source['id']], 'en', 'news')
    version = editorial.save_version(draft['id'], 0, ['A useful technical update.'])
    editorial.approve(draft['id'], version['id'])
    return draft, version
