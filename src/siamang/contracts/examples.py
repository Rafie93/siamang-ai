"""Access to the bundled contract v0.1 examples."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def example_names() -> list[str]:
    root = resources.files("siamang.contracts") / "examples"
    return sorted(p.name for p in root.iterdir() if p.name.endswith(".json"))


def load_example(name: str) -> dict[str, Any]:
    root = resources.files("siamang.contracts") / "examples"
    return json.loads((root / name).read_text(encoding="utf-8"))


def load_examples() -> dict[str, dict[str, Any]]:
    return {name: load_example(name) for name in example_names()}
