from tests.test_auth import client, login


def test_editorial_api_end_to_end(client):
    csrf = login(client)
    headers = {'X-CSRF-Token': csrf}
    source = client.post('/api/sources', json={'url': 'https://example.com/r', 'title': 'Release', 'body': 'Official notes'}, headers=headers).json()
    draft = client.post('/api/drafts', json={'source_ids': [source['id']], 'column': 'news', 'output_language': 'zh-CN'}, headers=headers).json()
    version = client.put(f"/api/drafts/{draft['id']}/content", json={'expected_version': 0, 'segments': ['技术更新']}, headers=headers).json()
    response = client.post(f"/api/drafts/{draft['id']}/approve", json={'version_id': version['id']}, headers=headers)
    assert response.status_code == 200
    assert client.get(f"/api/drafts/{draft['id']}").json()['status'] == 'approved'
    for path in ['/', '/sources', '/drafts', f"/drafts/{draft['id']}", '/calendar', '/settings']:
        assert client.get(path).status_code == 200


def test_generation_unconfigured_is_explicit(client):
    csrf = login(client)
    headers = {'X-CSRF-Token': csrf}
    source = client.post('/api/sources', json={'url': 'https://example.com/a', 'title': 'Release', 'body': 'Notes'}, headers=headers).json()
    draft = client.post('/api/drafts', json={'source_ids': [source['id']], 'column': 'news', 'output_language': 'en'}, headers=headers).json()
    response = client.post(f"/api/drafts/{draft['id']}/generate", json={}, headers=headers)
    assert response.status_code == 503
