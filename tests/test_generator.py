from dataclasses import replace
import httpx
import pytest
from app.adapters.generator import Generator
from app.errors import ServiceError


def test_generator_real_protocol_and_sources(settings):
    def respond(request):
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"segments":["Update"],"source_ids":[1],"claim_refs":[],"editor_notes":"Check"}'}}], 'usage': {'total_tokens': 10}})
    generator = Generator(replace(settings, llm_base_url='https://model.example/v1', llm_api_key='secret', llm_model='configured'), httpx.MockTransport(respond))
    result = generator.generate({'topics': 'Technology'}, [{'id': 1, 'body': 'Notes'}], 'news', 'en')
    assert result['segments'] == ['Update']
    assert result['usage']['total_tokens'] == 10


@pytest.mark.parametrize('content', ['{}', '{"segments":[""],"source_ids":[1]}', '{"segments":["Text"],"source_ids":[99]}'])
def test_invalid_generation_rejected(settings, content):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': {'content': content}}]}))
    generator = Generator(replace(settings, llm_base_url='https://model.example/v1', llm_api_key='secret', llm_model='model'), transport)
    with pytest.raises(ServiceError):
        generator.generate({}, [{'id': 1, 'body': 'Notes'}], 'news', 'en')
