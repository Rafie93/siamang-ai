"""Bounded non-streaming Ollama /api/chat adapter; no SDK or automatic fallback."""
import ipaddress
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from siamang.jsondata import thaw
from siamang.models.definition import HealthStatus, ProviderHealth
from siamang.models.errors import (GenerationError, InvalidModelRequestError, ModelTimeoutError,
                                   ModelUnavailableError, ProviderUnavailableError)
from siamang.models.provider import ModelProvider
from siamang.models.request import MODEL_TIMEOUT_SECS, ResponseFormat
from siamang.models.response import FinishReason, ProviderOutput, Usage

MAX_RESPONSE = 8 * 1024 * 1024


def _why(reason):
    """A short cause such as ``ConnectionRefusedError [Errno 61] Connection refused``: the type and the operating
    system's own errno text only, never an exception's free text (it may carry what it was given)."""
    name = type(reason).__name__
    if isinstance(reason, OSError) and isinstance(reason.errno, int):
        return f'{name} [Errno {reason.errno}] {os.strerror(reason.errno)}'
    return name


def _error_text(value, depth=0):
    """The message in ``"text"``, ``{"error": ...}`` or ``{"message": ...}``, also when the text is itself JSON:
    newer Ollama passes its runner's own error object on as the ``error`` string."""
    if isinstance(value, str):
        if depth < 6 and value.lstrip().startswith('{'):
            try:
                return _error_text(json.loads(value), depth + 1) or value
            except ValueError:
                pass
        return value
    if isinstance(value, dict) and depth < 6:
        for key in ('error', 'message'):
            found = _error_text(value.get(key), depth + 1)
            if found:
                return found
    return None


def _http_error(exc):
    """Ollama's own error text with the status. 4xx is this request's fault and is not retried."""
    try:
        detail = _error_text(json.loads(exc.read(64 * 1024)))
    except Exception:  # noqa: BLE001 - the status alone still says enough
        detail = None
    detail = ' '.join(detail.split())[:300] if isinstance(detail, str) else (exc.reason or '')
    message = f'Ollama HTTP {exc.code}: {detail}'.rstrip(': ')
    if exc.code == 404:
        return ModelUnavailableError(message)
    if 400 <= exc.code < 500:
        return InvalidModelRequestError(message)
    return ProviderUnavailableError(message)


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
        except urllib.error.HTTPError as exc:  # before URLError: Ollama answered, and its answer says why
            raise _http_error(exc) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ModelTimeoutError('Ollama request timed out') from None
            raise ProviderUnavailableError(f'Ollama request failed: {_why(exc.reason)}') from None
        except OSError as exc:
            raise ProviderUnavailableError(f'Ollama request failed: {_why(exc)}') from None
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
        if model.context_window:
            # Without num_ctx Ollama loads the model with its own default (4096 tokens) and refuses a longer prompt.
            options['num_ctx'] = model.context_window
        body = {'model': model.provider_model, 'messages': messages, 'stream': False, 'options': options}
        if request.response_format is ResponseFormat.JSON:
            body['format'] = thaw(request.json_schema) if request.json_schema else 'json'
        timeout = min(request.timeout_secs or MODEL_TIMEOUT_SECS, self.timeout_secs or MODEL_TIMEOUT_SECS, MODEL_TIMEOUT_SECS)
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
