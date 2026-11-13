from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from app.auth import hash_password
from app.main import create_app


@pytest.fixture
def client(settings):
    config = replace(settings, admin_password_hash=hash_password('a-long-test-password'))
    with TestClient(create_app(config)) as client:
        yield client


def login(client):
    client.get('/login')
    csrf = client.cookies['xops_csrf']
    response = client.post('/login', data={'password': 'a-long-test-password', 'csrf_token': csrf}, follow_redirects=False)
    assert response.status_code == 303
    return client.cookies['xops_csrf']


def test_auth_required(client):
    assert client.get('/api/sources').status_code == 401
    assert client.get('/').url.path == '/login'


def test_csrf_required(client):
    login(client)
    assert client.post('/api/sources', json={'url': 'https://example.com', 'title': 'T', 'body': 'B'}).status_code == 403


def test_source_html_is_escaped(client):
    csrf = login(client)
    response = client.post('/api/sources', json={'url': 'https://example.com', 'title': '<script>alert(1)</script>', 'body': 'Body'}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    html = client.get('/sources').text
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_health_no_secrets(client):
    response = client.get('/api/health')
    assert response.json() == {'status': 'ok'}
    assert 'password' not in response.text
