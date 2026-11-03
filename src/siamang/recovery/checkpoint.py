"""CheckpointStore: what each finished stage produced, so an interrupted run resumes instead of starting over.

One JSON file per run, replaced atomically. A damaged or foreign file is treated as "no checkpoint": a run that
cannot trust its past redoes it, and never skips a stage on a guess.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
VERSION = 1


class CheckpointStore:
    def __init__(self, directory: str | Path, max_age_secs: float | None = 6 * 3600):
        """A checkpoint older than ``max_age_secs`` is stale (the project may have moved on) and is ignored."""
        self._dir = Path(directory)
        self._max_age = max_age_secs

    def _file(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be a plain identifier")
        return self._dir / f"{run_id}.json"

    def load(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Stage name -> output of every stage that finished. Empty when there is nothing trustworthy."""
        path = self._file(run_id)
        try:
            if self._max_age is not None and time.time() - path.stat().st_mtime > self._max_age:
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return {}
        stages = data.get("stages") if isinstance(data, dict) and data.get("version") == VERSION else None
        if not isinstance(stages, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in stages.items()):
            return {}
        return stages

    def save(self, run_id: str, stage: str, output: dict[str, Any]) -> None:
        stages = self.load(run_id)
        stages[stage] = output
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".ckpt-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": VERSION, "stages": stages}, handle, sort_keys=True)
            os.replace(tmp, self._file(run_id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self, run_id: str) -> None:
        self._file(run_id).unlink(missing_ok=True)
