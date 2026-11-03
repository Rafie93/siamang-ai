"""``echo``: deterministic local adapter. Not an LLM.

Exists to exercise the provider boundary end to end (config -> registry ->
router -> generate) without network, model weights, or credentials. It
returns the last user message (or ``{"echo": ...}`` for JSON requests).
"""

from __future__ import annotations

import json

from siamang.models.definition import ModelDefinition
from siamang.models.provider import ModelProvider
from siamang.models.request import ModelRequest, ResponseFormat
from siamang.models.response import FinishReason, ProviderOutput, Usage


class EchoProvider(ModelProvider):
    def _generate(self, model: ModelDefinition, request: ModelRequest) -> ProviderOutput:
        text = request.messages[-1].content
        finish = FinishReason.STOP
        words = text.split()
        limit = request.options.max_tokens
        if limit is not None and len(words) > limit:
            words, finish = words[:limit], FinishReason.LENGTH
            text = " ".join(words)
        if request.response_format is ResponseFormat.JSON:
            content = json.dumps({"echo": text}, ensure_ascii=False)
        else:
            content = text
        prompt_words = sum(len(m.content.split()) for m in request.messages) + len((request.system_prompt or "").split())
        return ProviderOutput(content=content, finish_reason=finish, usage=Usage(prompt_words, len(words)))
