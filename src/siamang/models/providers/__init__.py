"""Provider adapters. The ONLY place vendor-specific code may live.

``PROVIDER_TYPES`` maps the config ``type`` string to an adapter class.
Phase 06 provides the local reference ``echo`` adapter. Phase 20 adds
``ollama`` for real HTTP generation, without changing router/request types.
``claude_cli`` runs the operator's own logged-in ``claude`` CLI the same way.
"""

from __future__ import annotations

from siamang.models.provider import ModelProvider
from siamang.models.providers.claude_cli import ClaudeCliProvider
from siamang.models.providers.echo import EchoProvider
from siamang.models.providers.ollama import OllamaProvider

PROVIDER_TYPES: dict[str, type[ModelProvider]] = {
    "claude_cli": ClaudeCliProvider,
    "echo": EchoProvider,
    "ollama": OllamaProvider,
}
