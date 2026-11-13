from dataclasses import replace
import httpx
import pytest
from app.adapters.postiz import Postiz
from app.errors import ServiceError


def test_schedule_contract(settings):
    def handle(request):
        assert request.headers['Authorization'] == 'key'
        assert request.url.path == '/public/v1/posts'
        return httpx.Response(200, json=[{'postId': 'remote-1', 'integration': 'channel'}])
    adapter = Postiz(replace(settings, postiz_base_url='https://p.example/public/v1', postiz_api_key='key', postiz_integration_id='channel'), httpx.MockTransport(handle))
    assert adapter.schedule('Update', '2027-01-01T09:00:00+00:00', 'channel') == 'remote-1'


@pytest.mark.parametrize('code, uncertain', [(401, False), (429, False), (500, True)])
def test_create_errors(settings, code, uncertain):
    adapter = Postiz(replace(settings, postiz_base_url='https://p.example/public/v1', postiz_api_key='key', postiz_integration_id='channel'), httpx.MockTransport(lambda r: httpx.Response(code)))
    with pytest.raises(ServiceError) as error:
        adapter.schedule('Update', '2027-01-01T09:00:00+00:00', 'channel')
    assert error.value.uncertain is uncertain
