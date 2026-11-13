from datetime import datetime, timedelta, timezone
import pytest
from app.errors import DomainError
from app.publications import Publications


def future():
    return (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()


def test_duplicate_intent_is_same_record(db, approved):
    _, version = approved
    service = Publications(db)
    first = service.schedule(version['id'], future(), 'channel')
    second = service.schedule(version['id'], future(), 'channel')
    assert first['id'] == second['id']


def test_edit_requires_new_approval(db, editorial, approved):
    draft, _ = approved
    new = editorial.save_version(draft['id'], 1, ['Changed'])
    with pytest.raises(DomainError):
        Publications(db).schedule(new['id'], future(), 'channel')


def test_pause_blocks_new_intents(db, approved):
    with db.transaction() as conn:
        conn.execute('UPDATE accounts SET paused=1')
    with pytest.raises(DomainError):
        Publications(db).schedule(approved[1]['id'], future(), 'channel')


def test_thread_cannot_be_scheduled(db, editorial, approved):
    draft, _ = approved
    version = editorial.save_version(draft['id'], 1, ['One', 'Two'])
    editorial.approve(draft['id'], version['id'])
    with pytest.raises(DomainError):
        Publications(db).schedule(version['id'], future(), 'channel')


def test_explicit_new_time_after_confirmed_cancellation(db, approved):
    service = Publications(db)
    first = service.schedule(approved[1]['id'], future(), 'channel')
    service.cancel(first['id'])
    new_time = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    second = service.schedule(approved[1]['id'], new_time, 'channel')
    assert second['id'] != first['id']
    assert second['status'] == 'ready'
