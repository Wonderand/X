import pytest
from app.errors import DomainError


def test_approval_does_not_follow_edit(editorial, approved):
    draft, v1 = approved
    v2 = editorial.save_version(draft['id'], 1, ['Updated text'])
    assert editorial.is_approved(v1['id'])
    assert not editorial.is_approved(v2['id'])
    with pytest.raises(DomainError):
        editorial.approve(draft['id'], v1['id'])


def test_concurrent_edit_conflicts(editorial, approved):
    draft, _ = approved
    editorial.save_version(draft['id'], 1, ['Winner'])
    with pytest.raises(DomainError) as error:
        editorial.save_version(draft['id'], 1, ['Stale'])
    assert error.value.status == 409


def test_invalid_language_and_unknown_source(editorial):
    with pytest.raises(DomainError):
        editorial.create_draft([999], 'en', 'news')
    source = editorial.add_source('https://example.com/a', 'Title', 'Body')
    with pytest.raises(DomainError):
        editorial.create_draft([source['id']], '', 'news')
