from datetime import timedelta
from urllib.parse import quote
import httpx
from app.errors import ServiceError
from app.publications import parse_time


class Postiz:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def request(self, method, path, **kwargs):
        if not self.settings.publisher_ready:
            raise ServiceError('publisher_not_configured')
        try:
            with httpx.Client(transport=self.transport, timeout=30, follow_redirects=False) as client:
                response = client.request(method, self.settings.postiz_base_url.rstrip('/') + path,
                                          headers={'Authorization': self.settings.postiz_api_key}, **kwargs)
        except httpx.RequestError:
            raise ServiceError('postiz_transport_error', uncertain=True) from None
        if response.status_code >= 300:
            retry = response.headers.get('Retry-After', '300')
            raise ServiceError(f'postiz_http_{response.status_code}', uncertain=response.status_code >= 500 or 300 <= response.status_code < 400,
                               retry_after=min(3600, max(30, int(retry))) if retry.isdigit() else 300)
        try:
            return response.json()
        except ValueError:
            raise ServiceError('postiz_invalid_response', uncertain=True) from None

    def schedule(self, text, scheduled_at, target):
        data = self.request('POST', '/posts', json={
            'type': 'schedule', 'date': scheduled_at, 'shortLink': False, 'tags': [],
            'posts': [{'integration': {'id': target}, 'value': [{'content': text, 'image': []}],
                       'settings': {'__type': 'x', 'who_can_reply_post': 'everyone'}}],
        })
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or not isinstance(data[0].get('postId'), str) or not data[0]['postId'] or data[0].get('integration') != target:
            raise ServiceError('postiz_invalid_create_response', uncertain=True)
        return data[0]['postId']

    def inspect(self, publication):
        when = parse_time(publication['scheduled_at'])
        data = self.request('GET', '/posts', params={'startDate': (when - timedelta(days=1)).isoformat(), 'endDate': (when + timedelta(days=1)).isoformat()})
        if not isinstance(data, dict) or not isinstance(data.get('posts'), list):
            raise ServiceError('postiz_invalid_list_response')
        matches = []
        for row in data['posts']:
            if not isinstance(row, dict) or not isinstance(row.get('integration'), dict) or row['integration'].get('id') != publication['target']:
                continue
            if publication.get('remote_id'):
                matched = row.get('id') == publication['remote_id']
            else:
                try:
                    matched = row.get('content') == publication['segments'][0] and abs((parse_time(row.get('publishDate')) - when).total_seconds()) < 1
                except Exception:
                    matched = False
            if matched:
                matches.append(row)
        if len(matches) != 1:
            raise ServiceError('postiz_no_unique_match')
        if not isinstance(matches[0].get('id'), str) or not matches[0]['id']:
            raise ServiceError('postiz_invalid_remote_id')
        return matches[0]

    def cancel(self, remote_id):
        result = self.request('DELETE', '/posts/' + quote(remote_id, safe=''))
        if not isinstance(result, dict) or result.get('id') != remote_id:
            raise ServiceError('postiz_cancel_unconfirmed', uncertain=True)
