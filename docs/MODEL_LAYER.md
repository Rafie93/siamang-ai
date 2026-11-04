# Model Provider + Model Router (Phase 06)

**Status:** implemented (`src/siamang/models/`). Runtime is still stdlib-only.

Phase 06 builds the vendor-neutral layer that future components (Planner,
System Analyst, Programmer, QA, Reviewer, Orchestrator) will use to talk to
language models. None of those components exist yet.

> **This layer does NOT implement agents.**
> **This layer does NOT implement orchestration.**
> **This layer does NOT execute tools.**
> It also adds no contract event types, no task events, and no real vendor integration.

## 1. Dependency direction

```
 future callers (Phase 11+)          siamang.models.factory  ◄── siamang.config
            │                                │ (config → registry)
            ▼                                ▼
     ModelRouter ──────► RoutingPolicy   ModelRegistry
            │  select() only                 │
            └──────────────┬─────────────────┘
                           ▼
           ModelProvider  (abstract boundary: provider.py)
           request / response / errors / definition / capabilities
                           ▲
                           │ subclass
           siamang.models.providers.*   ← the ONLY place vendor code may live
           (Phase 06: echo)
```

Rules, enforced by `tests/unit/test_models_architecture.py`:

* No module in `src/` imports a vendor SDK.
* The neutral modules (`capabilities`, `credentials`, `definition`, `errors`,
  `request`, `response`, `provider`, `registry`, `policy`, `router`) never
  import `siamang.models.providers` or `factory`.
* The router imports only the abstractions.
* Only `factory` and `config` know adapter types. `config` uses them only to
  validate `type = "..."` names.
* Phase 3 core (`bus`, `contracts`, `storage`, `ids`) does not depend on
  `siamang.models`.
* Adapters override hooks (`_generate`, `_health`, `_normalize_error`), never
  the public `generate` / `health` / `list_models`.
* `router.py` and `policy.py` contain no vendor or model names.

## 2. Types

| Type | Module | Notes |
|---|---|---|
| `Capability` | `capabilities.py` | `text_generation`, `structured_output`, `tool_calling`, `vision`, `streaming`, `reasoning`. Adding a member is additive. Unknown names are rejected (strict). |
| `ModelDefinition` | `definition.py` | `model_id` (SIAMANG's unique ID), `provider_id`, `display_name`, `provider_model` (the provider's own name/tag), `capabilities`, `context_window`, `priority` (lower = preferred), `enabled`, `metadata` (read-only; credential-looking keys rejected). `supports_streaming/tools/structured_output/vision/reasoning` are **derived** from `capabilities`, so they can never disagree. |
| `ProviderInfo` | `definition.py` | `provider_id`, `type`, `name`, `enabled`. No credential field. |
| `ProviderHealth` | `definition.py` | `status` (`available`/`degraded`/`unavailable`), `detail`, `available_models` (provider model names, or `None` = not reported), `checked_at`. `degraded` is still usable. |
| `ModelRequest` | `request.py` | `messages` (`user`/`assistant`; last must be `user`), `request_id` (`mrq_<ULID>` default), optional `model_id`, `required_capabilities`, `system_prompt`, `options` (`temperature`, `max_tokens`, `top_p`, `stop`), `response_format` (`text`/`json`), `json_schema`, `metadata`, `timeout_secs`. `effective_capabilities` = required + `text_generation` (+ `structured_output` for JSON). Invalid input raises `InvalidModelRequestError`. |
| `ModelResponse` | `response.py` | `request_id`, `provider_id`, `model_id`, `content`, `structured_output`, `finish_reason` (`stop`/`length`/`content_filter`/`tool_call`/`other`), `usage` (`input_tokens`, `output_tokens`, `total_tokens`), `metadata` (`provider_type`, `provider_model`, `duration_ms`), `raw_provider_metadata` (debug only; excluded from `to_dict()`; core must not read it). |
| `ProviderOutput` | `response.py` | What an adapter returns. The base class builds the `ModelResponse`, so IDs cannot be mislabelled. |

`ModelRequest.request_id` identifies one model call. It is **not** the
contract's `request_id` (the client's submission ID). A future Task Engine may
record both.

## 3. Provider abstraction (`provider.py`)

`ModelProvider` is an ABC. Its public methods are template methods:

* `list_models()`: the models this provider declares (from config).
* `health()`: **never raises**. Exceptions become `UNAVAILABLE`, and a
  disabled provider is `UNAVAILABLE` without being probed.
* `generate(model, request)`:
  1. type-checks arguments;
  2. checks the model belongs to this provider, matches a pinned
     `model_id`, provider enabled, capabilities present, and
     `max_tokens ≤ context_window`;
  3. calls the adapter's `_generate`;
  4. converts **any** exception into a `ModelError`. The error is raised
     outside the `except` block, so neither `__cause__` nor `__context__`
     holds the vendor exception. Only its type name is kept, in `error.cause`;
  5. parses JSON for `response_format="json"` when the adapter did not;
  6. builds the `ModelResponse`.

Adapters implement `_generate`, and optionally `_health` and
`_normalize_error` (vendor exception → `ModelError`). `timeout_secs` (on the
request, else the provider default) is passed through. Enforcing it is the
adapter's job, because the base class does not spawn threads. The `claude_cli` and
`ollama` adapters use the smallest of the request's value, the provider's value, and
600 seconds, with 120 seconds standing in for a value that is not set.

**Phase 06 adapter:** `echo` (`providers/echo.py`). It is local and
deterministic, and it is not an LLM. It returns the last user message
(`{"echo": ...}` for JSON) and honours `max_tokens` by words. It exists so
config → registry → router → generate can be exercised without network or
credentials. No commercial vendor adapter was built.

**`claude_cli` adapter** (`providers/claude_cli.py`). Runs the operator's own
logged-in `claude -p` as a subprocess, so requests count against their Claude
subscription. It is for one operator's personal use: Anthropic does not permit
[routing other users' requests through subscription credentials](https://code.claude.com/docs/en/legal-and-compliance.md).
Anything hosted or shared needs an API-key adapter instead.

* The child gets an allowlisted environment, so `ANTHROPIC_API_KEY` cannot
  silently switch billing to the API. Health is unavailable unless the login is
  a subscription. To use `claude setup-token` instead, set
  `api_key_env = "CLAUDE_CODE_OAUTH_TOKEN"`; the health check then accepts it.
* Each call runs in an empty temporary directory with no tools, no MCP servers,
  no user or project settings, and no saved session. `--bare` is not used because
  it does not read subscription credentials.
* `temperature`, `top_p`, `stop`, and `max_tokens` have no CLI flag and are not
  applied. Models run with their default sampling.
* A response from a model other than `provider_model` is an error. Hitting the
  plan limit is `rate_limited`.

## 3a. Choosing models (`siamang.toml`)

`siamang.toml` (not committed; template in `siamang.example.toml`) is the one file
for backends and models. `scripts/run-local.sh` and the Virtual Office both load
it. `[agents.<agent>] model = "<model id>"` selects the model for lead, planner,
analyst, programmer, qa, or reviewer; change that line and restart to switch
between Claude and Ollama. Only the chosen model is registered for that agent, so
a failure never moves work to another model.

## 4. Errors (`errors.py`)

All failures crossing the boundary are `ModelError` subclasses, each with a
typed `code` and a default `retryable`:

| code | class | retryable |
|---|---|---|
| `provider_unavailable` | `ProviderUnavailableError` | yes |
| `model_unavailable` | `ModelUnavailableError` | no |
| `authentication_error` | `ModelAuthenticationError` | no |
| `invalid_request` | `InvalidModelRequestError` | no |
| `timeout` | `ModelTimeoutError` | yes |
| `rate_limited` | `RateLimitedError` | yes |
| `context_limit` | `ContextLimitError` | no |
| `generation_error` | `GenerationError` | no |
| `unsupported_capability` | `UnsupportedCapabilityError` | no |
| `unknown_error` | `UnexpectedModelError` | no |

Each error carries `provider_id`, `model_id`, `request_id`, `cause`, and
`to_dict()`. Default mapping for exceptions an adapter did not translate:
`TimeoutError` → timeout, `ConnectionError`/`OSError` →
provider_unavailable, anything else → unknown_error.

Registry problems (duplicates, unknown IDs) are **not** model errors. They
are `RegistryError` subclasses, because they indicate programming or config
mistakes.

## 5. Registry (`registry.py`)

In-memory only. All listings are sorted by ID.

| Situation | Behavior |
|---|---|
| Register provider | Registers the provider **and** all its `list_models()`, atomically |
| Duplicate provider ID | `DuplicateProviderError` |
| Duplicate model ID (global, across providers) | `DuplicateModelError`; nothing registered |
| Model for unknown provider | `ProviderNotFoundError` |
| Unknown provider / model lookup | `ProviderNotFoundError` / `ModelNotFoundError` (both also `LookupError`) |
| Disabled model | Stays registered and listed (`enabled=False`), never selected. `disable_model` / `enable_model` / `remove_model` |
| Capability lookup | `find_by_capabilities(caps)`: models with **all** caps, enabled only by default |

## 6. Router and policy (`router.py`, `policy.py`)

`router.select(request) -> Selection(model, provider, reason, rejected)`.
The router **never generates**. `selection.generate(request)` is a
convenience for `selection.provider.generate(selection.model, request)`.

Decision order:

1. **Explicit `model_id`**: used as-is or rejected, with **no fallback** to
   another model. Errors: unknown → `model_unavailable`; disabled →
   `model_unavailable`; missing capability → `unsupported_capability`;
   provider down → `provider_unavailable`.
2. **Required capabilities**: none of the registered models has them →
   `unsupported_capability`.
3. **Enabled**: all capable models disabled → `model_unavailable`.
4. **Provider available**: provider disabled, unhealthy, or not serving
   `provider_model` → filtered out; nothing left → `provider_unavailable`.
   Health is probed at most once per provider per `select()`.
   `router.health_check = false` skips probes (disabled providers are still
   filtered).
5. **Policy**: `PriorityPolicy` orders by `priority` (lower first),
6. **then tie-breaker**: `model_id`, then `provider_id`. `model_id` is
   unique, so the order is total and independent of registration order.

No randomness, load balancing, cost, latency, or AI-based routing.
`RoutingPolicy` is a `Protocol`, so later policies can be plugged in
without touching the router.

## 7. Configuration

Extends Phase 3 config (defaults < TOML < env; unknown keys are errors):

```toml
[router]
health_check = true                 # env: SIAMANG_ROUTER_HEALTH_CHECK

[providers.local]                   # table key = provider_id
type = "echo"                       # required; adapter type
name = "Local echo"
enabled = true
api_key_env = "SIAMANG_LOCAL_KEY"   # NAME of an env var; the value is never in config
base_url = "http://127.0.0.1:1234"  # optional; for future adapters
timeout_secs = 60

[models.echo-small]                 # table key = model_id
provider = "local"                  # required
display_name = "Echo (small)"
provider_model = "echo-small"       # default: model_id
capabilities = ["text_generation", "structured_output"]   # default: ["text_generation"]
context_window = 4096
priority = 100                      # lower = preferred
enabled = true
metadata = { family = "echo" }      # credential-looking keys rejected
```

* `[providers.*]` and `[models.*]` come from the file only. Env overrides
  apply to flat sections (`[router]` included), because IDs may contain
  characters that cannot appear in env var names.
* Validation covers: required keys, value types, known provider `type`, known
  capabilities, model → provider references, `api_key_env` is a valid env
  var name, positive `context_window`/`timeout_secs`, ID format.
* A literal `api_key = "..."` is rejected with a hint to use `api_key_env`.
* `siamang.models.factory.build_registry(config, env)` /
  `build_router(...)` turn config into objects. Credentials are resolved
  there.
* Default config has **no** providers or models.

## 8. Security boundary

* Credentials exist only as `Secret` objects inside a provider. `str`/`repr`
  never show the value, pickling raises, and `reveal()` is meant for the
  transport call only.
* `ProviderInfo`, `ModelDefinition`, `ModelResponse.to_dict()`, `describe()`
  and `siamang models` expose only the env var **name** and whether it is set.
* Error messages pass through `log.redact()` (key=value / Bearer patterns),
  and the provider additionally scrubs **its own credential value** from
  every error message and health detail.
* Model and request metadata reject credential-looking keys.
* Tests: `test_models_security.py` checks logs, metadata, exceptions
  (including formatted tracebacks), and health, and scans source for
  key-shaped strings. A mutation check (disabling the scrubber) makes 5
  of those tests fail.

## 9. CLI

* `siamang models`: lists configured providers and models. Read-only, never
  calls a model.
* `siamang models --check`: also runs provider health probes; exit 1 if
  any provider is unavailable.
* `siamang health` / `siamang validate`: new `Models` line (builds the
  registry; no probes).

## 10. Testing strategy

* `tests/support/fake_models.py`: `FakeProvider`, a scriptable adapter
  (health status, served models, health exception, generate exception of
  any type, custom reply) plus vendor-like exception classes and a fake
  secret.
* Files: `test_models_types`, `_registry`, `_router`, `_generation`,
  `_config`, `_security`, `_architecture`.
* The Phase 3 autouse fixture blocks every socket connect, so any network
  attempt fails the test. No test needs an API key, Ollama, or internet.
