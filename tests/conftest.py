import json
import logging
import os
import socket
from pathlib import Path

import pytest

from siamang.contracts.examples import load_examples

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Every test runs offline and without the user's Siamang environment."""

    def _no_network(*args, **kwargs):
        raise RuntimeError("network access is not allowed in Siamang tests")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    for key in list(os.environ):
        if key.startswith("SIAMANG_"):
            monkeypatch.delenv(key)
    yield
    logger = logging.getLogger("siamang")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


@pytest.fixture
def examples():
    return load_examples()


@pytest.fixture
def db(tmp_path):
    from siamang.storage import Database

    database = Database(tmp_path / "siamang.db")
    yield database
    database.close()


@pytest.fixture
def config_file(tmp_path):
    def write(text: str, name: str = "siamang.toml") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return write


def load_fixture(*parts: str):
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))
