import pytest
from app.config import Settings


def test_missing_services_are_not_ready(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert not settings.generator_ready
    assert not settings.publisher_ready
    assert not settings.auth_ready


def test_file_cannot_be_data_root(tmp_path):
    path = tmp_path / 'file'
    path.write_text('x')
    with pytest.raises(ValueError):
        Settings(data_dir=path)
