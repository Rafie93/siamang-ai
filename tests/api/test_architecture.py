"""Optional transport is strictly downstream; no core dependency on FastAPI or runtime."""
import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / 'src' / 'siamang'


def test_core_has_no_transport_imports():
    for path in SRC.rglob('*.py'):
        if path.is_relative_to(SRC / 'api'):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n.startswith(('siamang.api', 'fastapi', 'uvicorn', 'pydantic')) for n in names), path


def test_runtime_has_no_patch_writer_or_shell_entrypoint():
    for path in (SRC / 'api').glob('*.py'):
        if path.name == 'main.py':
            continue
        tree = ast.parse(path.read_text())
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not calls & {'system', 'Popen', 'apply', 'commit', 'deploy', 'exec', 'eval'}, path
