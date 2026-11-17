"""Bounded non-streaming Ollama /api/chat adapter; no SDK or automatic fallback."""
import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request

from siamang.jsondata import thaw
from siamang.models.definition import HealthStatus, ProviderHealth
from siamang.models.errors import GenerationError, ModelTimeoutError, ProviderUnavailableError
from siamang.models.provider import ModelProvider
from siamang.models.request import ResponseFormat
from siamang.models.response import FinishReason, ProviderOutput, Usage

MAX_RESPONSE = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward a request or credential to another origin


class OllamaProvider(ModelProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base = self.base_url or 'http://127.0.0.1:11434'
        url = urllib.parse.urlsplit(base)
        try:
            loopback = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname).is_loopback
        except (TypeError, ValueError):
            loopback = False
        if (url.username or url.password or url.query or url.fragment or url.path not in ('', '/')
                or url.scheme not in ('http', 'https') or not url.hostname
                or (url.scheme == 'http' and not loopback)):
            raise ValueError('Ollama URL requires HTTPS or a loopback HTTP origin')
        self.base_url = base.rstrip('/')

    def _request(self, path, body=None, timeout=5):
        headers = {'Content-Type': 'application/json'}
        if self._credential is not None:
            headers['Authorization'] = 'Bearer ' + self._credential.reveal()
        req = urllib.request.Request(self.base_url + path,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers=headers)
        # No ambient proxy routing for a local agent connection.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=timeout) as response:
                data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise GenerationError('model response exceeds size limit')
            return json.loads(data)
        except TimeoutError:
            raise ModelTimeoutError('Ollama request timed out') from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ModelTimeoutError('Ollama request timed out') from None
            raise ProviderUnavailableError('Ollama request failed') from None
        except OSError:
            raise ProviderUnavailableError('Ollama request failed') from None
        except (UnicodeError, ValueError):
            raise GenerationError('invalid Ollama response') from None

    def _health(self):
        data = self._request('/api/tags')
        if not isinstance(data, dict) or not isinstance(data.get('models'), list):
            raise ProviderUnavailableError('invalid Ollama model catalog')
        names = frozenset(m['name'] for m in data['models'] if isinstance(m, dict) and isinstance(m.get('name'), str))
        return ProviderHealth(self.provider_id, HealthStatus.AVAILABLE, available_models=names)

    def _generate(self, model, request):
        messages = [{'role': str(m.role), 'content': m.content} for m in request.messages]
        if request.system_prompt:
            messages.insert(0, {'role': 'system', 'content': request.system_prompt})
        options = {}
        for source, target in [('temperature', 'temperature'), ('max_tokens', 'num_predict'), ('top_p', 'top_p')]:
            value = getattr(request.options, source, None)
            if value is not None:
                options[target] = value
        body = {'model': model.provider_model, 'messages': messages, 'stream': False, 'options': options}
        if request.response_format is ResponseFormat.JSON:
            body['format'] = thaw(request.json_schema) if request.json_schema else 'json'
        timeout = min(request.timeout_secs or 120, self.timeout_secs or 120, 600)
        data = self._request('/api/chat', body, timeout)
        if (not isinstance(data, dict) or data.get('done') is not True
                or not isinstance(data.get('message'), dict) or not isinstance(data['message'].get('content'), str)):
            raise GenerationError('Ollama did not return a completed text response')
        counts = [data.get('prompt_eval_count'), data.get('eval_count')]
        valid = not any(v is not None and (type(v) is not int or v < 0) for v in counts)
        if data.get('model') != model.provider_model:
            error = GenerationError('Ollama returned another model')
            error.usage = Usage(*counts) if valid else None  # the other model still ran, and its tokens were spent
            raise error
        if not valid:
            raise GenerationError('invalid Ollama token usage')
        reason = FinishReason.LENGTH if data.get('done_reason') == 'length' else FinishReason.STOP
        # Measured against a real server: the prompt count stays whole when Ollama reuses its prompt cache.
        return ProviderOutput(data['message']['content'], reason, Usage(*counts))
