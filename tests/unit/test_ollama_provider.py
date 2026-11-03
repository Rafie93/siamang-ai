import json
import pytest

from siamang.models.definition import ModelDefinition, ProviderInfo
from siamang.models.providers.ollama import OllamaProvider
from siamang.models.request import ModelRequest, ResponseFormat
from siamang.models.errors import GenerationError, ProviderUnavailableError


def provider(url=None):
    model = ModelDefinition('local-model', 'local', {'text_generation', 'structured_output'}, provider_model='test-model')
    return OllamaProvider(ProviderInfo('local', 'ollama'), [model], base_url=url), model


@pytest.mark.parametrize('url', ['http://example.com', 'file:///tmp/model', 'http://u:p@localhost:11434',
                               'http://localhost:11434?key=secret', 'http://localhost:11434/api',
                               'https://example.com/#fragment'])
def test_ollama_rejects_unsafe_server_urls(url):
    with pytest.raises(ValueError):
        provider(url)


@pytest.mark.parametrize('url', [None, 'http://127.0.0.1:11434', 'http://localhost:11434',
                               'http://[::1]:11434', 'https://example.com'])
def test_ollama_supports_loopback_or_https(url):
    p, _ = provider(url)
    assert p.base_url


def test_ollama_maps_real_http_contract_without_network(monkeypatch):
    p, model = provider()
    calls = []
    def exchange(path, body=None, timeout=5):
        calls.append((path, body, timeout))
        return {'done': True, 'model': 'test-model', 'message': {'content': '{"ok":true}'},
                'done_reason': 'stop', 'prompt_eval_count': 5, 'eval_count': 3}
    monkeypatch.setattr(p, '_request', exchange)
    response = p.generate(model, ModelRequest.from_prompt('hello', response_format=ResponseFormat.JSON, timeout_secs=10))
    assert response.structured_output == {'ok': True} and response.usage.total_tokens == 8
    assert calls[0][0] == '/api/chat' and calls[0][1]['stream'] is False and calls[0][2] == 10
    assert calls[0][1]['format'] == 'json'


@pytest.mark.parametrize('data', [{}, {'done': False, 'message': {'content': 'partial'}},
                                {'done': True, 'model': 'another', 'message': {'content': 'wrong'}},
                                {'done': True, 'model': 'test-model', 'message': {'content': []}},
                                {'done': True, 'model': 'test-model', 'message': {'content': 'ok'}, 'eval_count': -1}])
def test_ollama_does_not_claim_success_for_bad_responses(data, monkeypatch):
    p, model = provider()
    monkeypatch.setattr(p, '_request', lambda *a: data)
    with pytest.raises(GenerationError):
        p.generate(model, ModelRequest.from_prompt('hello'))


def test_ollama_health_reports_available_models(monkeypatch):
    p, model = provider()
    monkeypatch.setattr(p, '_request', lambda *a: {'models': [{'name': 'test-model'}]})
    assert p.health().serves(model)


def test_ollama_http_is_bounded_and_does_not_forward_redirects(monkeypatch):
    import io
    import urllib.request
    from siamang.models.providers.ollama import MAX_RESPONSE, NoRedirect
    p, model = provider()
    class Response(io.BytesIO):
        def read(self, size=-1):
            assert size == MAX_RESPONSE + 1
            return super().read(size)
    class Opener:
        def open(self, req, timeout):
            assert req.full_url == 'http://127.0.0.1:11434/api/chat' and timeout == 120
            assert json.loads(req.data)['stream'] is False
            return Response(json.dumps({'model': 'test-model', 'done': True,
                                        'message': {'content': 'hello'}}).encode())
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    assert p.generate(model, ModelRequest.from_prompt('hello')).content == 'hello'
    assert NoRedirect().redirect_request(None, None, 302, None, None, 'https://elsewhere') is None


def test_ollama_transport_errors_never_echo_server_exception(monkeypatch):
    import urllib.error
    import urllib.request
    from siamang.models.errors import ModelTimeoutError
    p, _ = provider()
    class Opener:
        def open(self, req, timeout):
            raise urllib.error.URLError(TimeoutError('credential material should never be echoed'))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(ModelTimeoutError) as info:
        p._request('/api/tags')
    assert 'credential material' not in str(info.value)


def test_approval_policy_reference_cannot_be_rebound(db):
    from siamang.approval import ApprovalService, ApprovalPolicy
    from siamang.projects import ProjectRegistry
    from siamang.tasks import TaskEngine
    service = ApprovalService(TaskEngine(db, ProjectRegistry(db)))
    with pytest.raises(AttributeError):
        service.policy = ApprovalPolicy(authorized_approvers={'mallory'})
