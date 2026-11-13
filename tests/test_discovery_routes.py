from tests.test_auth import client, login


def test_discovery_pages_and_csrf(client):
    assert client.get('/api/discovery/candidates').status_code == 401
    csrf = login(client)
    for path in ('/discovery', '/discovery/candidates'):
        assert client.get(path).status_code == 200
    assert client.post('/api/discovery/settings', json={'daily_limit': 5}).status_code == 403
    assert client.post('/api/discovery/settings', json={'daily_limit': 5}, headers={'X-CSRF-Token': csrf}).status_code == 200


def test_rule_run_and_unverified_candidate_boundary(client):
    csrf = login(client)
    headers = {'X-CSRF-Token': csrf}
    client.post('/api/discovery/settings', json={'daily_limit': 3}, headers=headers)
    response = client.post('/api/discovery/rules', json={'query': 'Rust compiler updates', 'backend': 'anysearch', 'days': 3, 'limit': 3}, headers=headers)
    assert response.status_code == 200
    saved = response.json()
    assert client.post(f"/api/discovery/rules/{saved['id']}/run", headers=headers).status_code == 503
    # No runtime: no paid reservation or fake search result.
    assert client.get('/api/discovery/runs').json() == []
