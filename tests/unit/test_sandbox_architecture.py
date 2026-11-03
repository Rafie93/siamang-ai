"""Dependency direction: verification -> sandbox -> {log, programmer.safety}; nothing in the core imports either one."""
import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "siamang"


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return {m for m in out if m.startswith("siamang")}


def test_sandbox_only_reaches_logging_and_path_rules():
    for path in (SRC / "sandbox").glob("*.py"):
        own = {f"siamang.sandbox.{p.stem}" for p in (SRC / "sandbox").glob("*.py")} | {"siamang.sandbox"}
        assert _imports(path) <= own | {"siamang.log", "siamang.programmer.safety"}, path.name


def test_verification_only_reaches_the_sandbox():
    for path in (SRC / "verification").glob("*.py"):
        assert all(m.startswith(("siamang.verification", "siamang.sandbox")) for m in _imports(path)), path.name


def test_only_the_api_runtime_uses_them():
    users = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
             if not p.is_relative_to(SRC / "sandbox") and not p.is_relative_to(SRC / "verification")
             and any(m.startswith(("siamang.sandbox", "siamang.verification")) for m in _imports(p))}
    assert users == {"api/runtime.py"}  # lazily, inside the method that builds the verifier


def test_only_the_runtime_spawns_containers():
    spawners = {p.relative_to(SRC).as_posix() for p in (SRC / "sandbox").glob("*.py") if "subprocess" in p.read_text()}
    assert spawners == {"sandbox/runtime.py"}
