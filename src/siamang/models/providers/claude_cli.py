"""Bounded `claude -p` adapter: the operator's own subscription login, no SDK or API key.

Personal use only: Anthropic does not permit routing other users' requests through
subscription credentials. See docs/MODEL_LAYER.md.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from siamang.jsondata import thaw
from siamang.models.definition import HealthStatus, ProviderHealth
from siamang.models.errors import (
    ContextLimitError,
    GenerationError,
    ModelAuthenticationError,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderUnavailableError,
    RateLimitedError,
)
from siamang.models.provider import ModelProvider
from siamang.models.request import ResponseFormat
from siamang.models.response import FinishReason, ProviderOutput, Usage

MAX_RESPONSE = 8 * 1024 * 1024
MODEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]*')
# Anything else (notably ANTHROPIC_API_KEY, which `-p` would silently bill) never reaches the child.
ENV_ALLOWLIST = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME')
DEFAULT_SYSTEM = 'You are a language model called by a program. Answer the request directly.'
JSON_ONLY = 'Respond with a single JSON value only, without Markdown fences or commentary.'


class ClaudeCliProvider(ModelProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.base_url:
            raise ValueError('claude_cli takes no base_url')
        for model in self._models:
            if not MODEL_RE.fullmatch(model.provider_model):
                raise ValueError(f'invalid provider_model for {model.model_id!r}')

    def _env(self):
        env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
        if self._credential is not None:
            env['CLAUDE_CODE_OAUTH_TOKEN'] = self._credential.reveal()
        return env

    def _binary(self, env):
        found = shutil.which('claude', path=env.get('PATH'))
        if found:
            return found
        fallback = Path.home() / '.local' / 'bin' / 'claude'
        if fallback.is_file():
            return str(fallback)
        raise ProviderUnavailableError('claude CLI is not installed')

    def _run(self, args, *, prompt=None, cwd=None, timeout=15):
        env = self._env()
        try:
            return subprocess.run([self._binary(env), *args], input=prompt, capture_output=True, encoding='utf-8',
                                  shell=False, check=False, cwd=cwd, env=env, timeout=timeout,
                                  stdin=subprocess.DEVNULL if prompt is None else None)
        except subprocess.TimeoutExpired:
            raise ModelTimeoutError('claude CLI timed out') from None
        except OSError:
            raise ProviderUnavailableError('claude CLI could not be started') from None
        except UnicodeError:
            raise GenerationError('invalid claude CLI output') from None

    def _health(self):
        try:
            status = json.loads(self._run(['auth', 'status']).stdout)
        except (TypeError, ValueError):
            raise ProviderUnavailableError('invalid claude CLI auth status') from None
        if not isinstance(status, dict) or status.get('loggedIn') is not True:
            raise ProviderUnavailableError('claude CLI is not logged in')
        if self._credential is None and status.get('authMethod') != 'claude.ai':
            raise ProviderUnavailableError('claude CLI is not using a subscription login')
        return ProviderHealth(self.provider_id, HealthStatus.AVAILABLE)

    def _generate(self, model, request):
        system = request.system_prompt or DEFAULT_SYSTEM
        args = ['-p', '--model', model.provider_model, '--output-format', 'json', '--tools', '',
                '--no-session-persistence', '--strict-mcp-config', '--setting-sources', '',
                '--system-prompt-file', 'system.txt']
        if request.response_format is ResponseFormat.JSON:
            if request.json_schema:
                args += ['--json-schema', json.dumps(thaw(request.json_schema))]
            else:
                system += '\n\n' + JSON_ONLY
        timeout = min(request.timeout_secs or 120, self.timeout_secs or 120, 600)
        # An empty working directory keeps the project's CLAUDE.md and files out of the session.
        with tempfile.TemporaryDirectory(prefix='siamang-claude-') as cwd:
            Path(cwd, 'system.txt').write_text(system, encoding='utf-8')
            done = self._run(args, prompt=_render(request.messages), cwd=cwd, timeout=timeout)
        return _output(model, done)


def _render(messages):
    if len(messages) == 1:
        return messages[0].content
    # JSON keeps a message's text from posing as another conversation turn.
    history = [{'role': str(m.role), 'content': m.content} for m in messages]
    return 'Conversation so far, as JSON. Reply to the final user message.\n' + json.dumps(history, ensure_ascii=False)


def _output(model, done):
    if len(done.stdout) > MAX_RESPONSE:
        raise GenerationError('model response exceeds size limit')
    try:
        data = json.loads(done.stdout.strip().rsplit('\n', 1)[-1])
    except (IndexError, ValueError):
        if done.returncode != 0:
            raise ProviderUnavailableError('claude CLI failed') from None
        raise GenerationError('invalid claude CLI response') from None
    if not isinstance(data, dict):
        raise GenerationError('invalid claude CLI response')
    if data.get('is_error') is not False or done.returncode != 0:
        raise _failure(data)
    if not isinstance(data.get('result'), str):
        raise GenerationError('claude CLI did not return a completed text response')
    served = data.get('modelUsage')
    # Aliases resolve to full IDs (sonnet -> claude-sonnet-5-5), so match on containment.
    if not isinstance(served, dict) or not any(model.provider_model in k or k in model.provider_model for k in served):
        raise GenerationError('claude CLI returned another model')
    structured = data.get('structured_output')
    content = data['result'] if structured is None else json.dumps(structured, ensure_ascii=False)
    reason = FinishReason.LENGTH if data.get('stop_reason') == 'max_tokens' else FinishReason.STOP
    return ProviderOutput(content, reason, _usage(data.get('usage')), structured,
                          {'served_models': sorted(served), 'list_cost_usd': data.get('total_cost_usd')})


def _usage(usage):
    if not isinstance(usage, dict):
        return Usage()
    counts = [usage.get(k) for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
    output = usage.get('output_tokens')
    if any(v is not None and (type(v) is not int or v < 0) for v in [*counts, output]):
        raise GenerationError('invalid claude CLI token usage')
    return Usage(sum(v or 0 for v in counts), output)


def _failure(data):
    """Classify from status and text, but never repeat the text: it may quote the prompt."""
    status, text = data.get('api_error_status'), str(data.get('result') or '').lower()
    if status == 429 or 'hit your' in text or 'rate limit' in text or 'usage limit' in text:
        return RateLimitedError('claude subscription limit reached')
    if status in (401, 403) or 'log in' in text or '/login' in text:
        return ModelAuthenticationError('claude CLI is not authenticated')
    if status == 404:
        return ModelUnavailableError('claude CLI does not serve this model')
    if status == 413 or 'prompt is too long' in text:
        return ContextLimitError('prompt exceeds the model context')
    if isinstance(status, int) and status >= 500:
        return ProviderUnavailableError('Anthropic service error')
    return GenerationError('claude CLI reported an error')
