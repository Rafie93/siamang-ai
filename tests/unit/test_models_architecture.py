"""Dependency direction: router -> provider abstraction -> adapters; never core -> vendor SDK."""

import ast
import inspect
from pathlib import Path

from siamang.models import ModelProvider
from siamang.models.providers import PROVIDER_TYPES

SRC = Path(__file__).parents[2] / "src" / "siamang"
MODELS = SRC / "models"

VENDOR_SDKS = {"openai", "anthropic", "ollama", "google", "vertexai", "mistralai", "cohere", "groq", "litellm",
               "langchain", "langchain_core", "llama_cpp", "transformers", "huggingface_hub", "boto3"}

# The provider-neutral core of the model layer.
NEUTRAL = ["capabilities", "credentials", "definition", "errors", "request", "response", "provider",
           "registry", "policy", "router"]


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def _module(path: Path) -> str:
    return ".".join(path.relative_to(SRC.parent).with_suffix("").parts)


def test_no_vendor_sdk_anywhere():
    for path in SRC.rglob("*.py"):
        roots = {m.split(".")[0] for m in _imports(path)}
        assert not roots & VENDOR_SDKS, path


def test_neutral_core_never_imports_adapters():
    for name in NEUTRAL:
        imports = _imports(MODELS / f"{name}.py")
        assert not any(m.startswith("siamang.models.providers") for m in imports), name
        assert not any(m.startswith("siamang.models.factory") for m in imports), name


def test_router_depends_only_on_abstractions():
    imports = {m for m in _imports(MODELS / "router.py") if m.startswith("siamang")}
    assert "siamang.models.provider" in imports  # router -> provider abstraction
    assert imports <= {
        "siamang.log",
        "siamang.models.capabilities",
        "siamang.models.definition",
        "siamang.models.errors",
        "siamang.models.policy",
        "siamang.models.provider",
        "siamang.models.registry",
        "siamang.models.request",
        "siamang.models.response",
    }


def test_only_factory_and_config_know_about_adapters():
    users = {
        _module(p)
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(MODELS / "providers")
        and any(m.startswith("siamang.models.providers") for m in _imports(p))
    }
    assert users == {"siamang.models.factory", "siamang.config"}  # config: type-name validation only


def test_phase3_core_does_not_depend_on_models():
    for sub in ("bus.py", "contracts", "storage", "ids.py"):
        target = SRC / sub
        for path in [target] if target.is_file() else target.rglob("*.py"):
            assert not any(m.startswith("siamang.models") for m in _imports(path)), path


def test_adapters_implement_the_abstraction():
    for type_name, cls in PROVIDER_TYPES.items():
        assert issubclass(cls, ModelProvider), type_name
        assert not inspect.isabstract(cls), type_name
        # adapters customise hooks only; the public boundary stays in the base class
        for public in ("generate", "health", "list_models"):
            assert public not in cls.__dict__, f"{type_name} overrides {public}"


def test_no_vendor_specific_routing():
    src = (MODELS / "router.py").read_text() + (MODELS / "policy.py").read_text()
    for word in ("gpt", "claude", "qwen", "llama", "gemini", "ollama", "openai", "anthropic"):
        assert word not in src.lower(), word
