"""Phase 3 boundary guards: stdlib-only core, no AI/agent code paths."""

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src" / "siamang"

FORBIDDEN = {
    "anthropic", "openai", "ollama", "google", "langchain", "crewai", "autogen",
    "transformers", "torch", "requests", "httpx", "aiohttp", "websockets", "fastapi", "flask",
}


def _imports():
    for path in SRC.rglob("*.py"):
        if path.is_relative_to(SRC / "api"):
            continue  # optional transport dependency; core remains stdlib-only
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield path, alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                yield path, node.module.split(".")[0]


def test_core_imports_only_stdlib_and_itself():
    allowed = set(sys.stdlib_module_names) | {"siamang"}
    bad = [(str(p.relative_to(ROOT)), m) for p, m in _imports() if m not in allowed]
    assert bad == []


def test_no_forbidden_packages_imported():
    assert not [m for _, m in _imports() if m in FORBIDDEN]


def test_no_runtime_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []


def test_no_out_of_scope_modules():
    names = {p.stem for p in SRC.rglob("*.py")}
    for word in ("agent", "planner", "orchestrator", "llm", "model_router", "bridge", "programmer", "reviewer"):
        assert not any(word in n for n in names), word
