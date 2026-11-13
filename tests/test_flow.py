from dataclasses import replace
import httpx
from fastapi.testclient import TestClient
from app.accounts import update_account
from app.auth import hash_password
from app.main import create_app
from app.publications import Publications
from app.worker import Worker
from tests.test_auth import login
from tests.test_publications import future


def test_http_editorial_to_verified_publication(settings):
    config = replace(settings, admin_password_hash=hash_password('a-long-test-password'), llm_base_url='https://model.example/v1', llm_api_key='key', llm_model='configured', postiz_base_url='https://postiz.example/public/v1', postiz_api_key='key', postiz_integration_id='channel')
    application = create_app(config)
    with TestClient(application) as client:
        db = application.state.db
        update_account(db, {'name': 'Tech', 'topics': 'Tools', 'audience': 'Developers', 'tone': 'Accurate', 'timezone': 'UTC', 'daily_limit': 2, 'generation_limit': 2})
        Publications(db).resume()
        csrf = login(client)
        headers = {'X-CSRF-Token': csrf}
        source = client.post('/api/sources', json={'url': 'https://example.com/release', 'title': 'Release', 'body': 'Release notes'}, headers=headers).json()
        draft = client.post('/api/drafts', json={'source_ids': [source['id']], 'column': 'news', 'output_language': 'en'}, headers=headers).json()
        assert client.post(f"/api/drafts/{draft['id']}/generate", headers=headers).status_code == 200
        generation_transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': {'content': '{"segments":["New developer tool update. https://example.com/release"],"source_ids":[1],"claim_refs":[],"editor_notes":"Check source"}'}}], 'usage': {'total_tokens': 30}}))
        requests = []
        def publishing(request):
            requests.append(request.method)
            if request.method == 'POST':
                return httpx.Response(200, json=[{'postId': 'remote', 'integration': 'channel'}])
            return httpx.Response(200, json={'posts': [{'id': 'remote', 'integration': {'id': 'channel'}, 'state': 'PUBLISHED', 'releaseURL': 'https://x.com/account/status/99999'}]})
        worker = Worker(db, config, generator_transport=generation_transport, postiz_transport=httpx.MockTransport(publishing))
        worker.run_once()
        current = client.get(f"/api/drafts/{draft['id']}").json()
        assert current['status'] == 'review_pending'
        version_id = current['versions'][0]['id']
        assert client.post(f"/api/drafts/{draft['id']}/approve", json={'version_id': version_id}, headers=headers).status_code == 200
        publication = client.post('/api/publications', json={'version_id': version_id, 'scheduled_at': future()}, headers=headers).json()
        worker.run_once()
        assert Publications(db).get(publication['id'])['status'] == 'scheduled'
        worker.run_once()
        result = client.get(f"/api/publications/{publication['id']}").json()
        assert result['status'] == 'published'
        assert result['platform_post_id'] == '99999'
        assert requests == ['POST', 'GET']
        assert db.one('SELECT status FROM usage_records')['status'] == 'completed'
