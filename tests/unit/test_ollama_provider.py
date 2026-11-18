import json
import pytest

from siamang.models.definition import ModelDefinition, ProviderInfo
from siamang.models.providers.ollama import OllamaProvider
from siamang.models.request import MODEL_TIMEOUT_SECS, ModelRequest, ResponseFormat
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


def test_ollama_reply_from_another_model_keeps_its_tokens(monkeypatch):
    p, model = provider()
    monkeypatch.setattr(p, '_request', lambda *a: {'done': True, 'model': 'another', 'message': {'content': 'x'},
                                                   'prompt_eval_count': 12, 'eval_count': 4})
    with pytest.raises(GenerationError) as caught:
        p.generate(model, ModelRequest.from_prompt('hello'))
    assert caught.value.usage.input_tokens == 12 and caught.value.usage.output_tokens == 4


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
            assert req.full_url == 'http://127.0.0.1:11434/api/chat' and timeout == MODEL_TIMEOUT_SECS
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


def test_ollama_sends_the_model_context_window_as_num_ctx(monkeypatch):
    model = ModelDefinition('local-model', 'local', {'text_generation'}, provider_model='test-model', context_window=16384)
    p = OllamaProvider(ProviderInfo('local', 'ollama'), [model])
    sent = []
    monkeypatch.setattr(p, '_request', lambda path, body=None, timeout=5: sent.append(body) or
                        {'done': True, 'model': 'test-model', 'message': {'content': 'ok'}})
    p.generate(model, ModelRequest.from_prompt('hello'))
    assert sent[0]['options']['num_ctx'] == 16384
    q, plain = provider()  # no context window configured: Ollama keeps its own default
    monkeypatch.setattr(q, '_request', lambda path, body=None, timeout=5: sent.append(body) or
                        {'done': True, 'model': 'test-model', 'message': {'content': 'ok'}})
    q.generate(plain, ModelRequest.from_prompt('hello'))
    assert 'num_ctx' not in sent[1]['options']


@pytest.mark.parametrize('code,body,kind,text', [
    (400, {'error': json.dumps({'error': {'code': 400, 'message': 'request (5109 tokens) exceeds the available '
                                          'context size (4096 tokens)', 'type': 'exceed_context_size_error'}})},
     'InvalidModelRequestError', 'Ollama HTTP 400: request (5109 tokens) exceeds the available context size (4096 tokens)'),
    (400, {'error': {'message': 'nested object'}}, 'InvalidModelRequestError', 'Ollama HTTP 400: nested object'),
    (404, {'error': 'model "x" not found'}, 'ModelUnavailableError', 'Ollama HTTP 404: model "x" not found'),
    (500, {'error': 'llama runner process has terminated'}, 'ProviderUnavailableError',
     'Ollama HTTP 500: llama runner process has terminated'),
])
def test_ollama_http_errors_keep_ollamas_reason(code, body, kind, text, monkeypatch):
    import io
    import urllib.error
    import urllib.request
    p, _ = provider()
    class Opener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, 'Status', {}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(Exception) as info:
        p._request('/api/chat', {})
    assert type(info.value).__name__ == kind and info.value.message == text


def test_ollama_connection_errors_name_only_the_os_reason(monkeypatch):
    import errno
    import urllib.error
    import urllib.request
    p, _ = provider()
    class Opener:
        def open(self, req, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'free text never echoed'))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(ProviderUnavailableError) as info:
        p._request('/api/tags')
    assert 'ConnectionRefusedError' in info.value.message and 'free text' not in info.value.message
