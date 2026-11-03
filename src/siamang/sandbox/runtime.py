"""Container runtimes. One shape (:class:`ContainerRuntime`) so tests and other engines can stand in for the CLI.

:class:`CliContainerRuntime` drives ``podman`` or ``docker``. What it guarantees on every run, whatever the caller
asks: no network, no capabilities, no privilege escalation, a read-only root filesystem, an unprivileged user, a
memory/CPU/process limit, a fixed minimal environment, and only the workspace copy mounted. The host process that
starts the container gets a minimal environment too, so operator variables never reach the container engine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from siamang.sandbox.policy import SandboxPolicy

SANDBOX_USER = "65534:65534"
CONTAINER_ENV = {"HOME": "/tmp", "CI": "1", "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
HOST_ENV_KEYS = ("PATH", "HOME", "XDG_RUNTIME_DIR", "DOCKER_HOST", "CONTAINER_HOST", "TMPDIR")


@dataclass(frozen=True)
class RawRun:
    exit_code: int | None  # None: no exit code (timed out, or the engine could not start it)
    output: str
    timed_out: bool = False
    truncated: bool = False
    seconds: float = 0.0


class ContainerRuntime(Protocol):
    def available(self) -> bool: ...

    def run(self, workspace: Path, argv: tuple[str, ...], policy: SandboxPolicy) -> RawRun: ...


class _Bounded:
    """Keeps the first ``limit`` bytes of a stream and drains the rest, so a noisy command cannot fill memory."""

    def __init__(self, stream, limit: int):
        self.data = bytearray()
        self.truncated = False
        self._limit = limit
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream) -> None:
        for chunk in iter(lambda: stream.read(8192), b""):
            room = self._limit - len(self.data)
            if room > 0:
                self.data += chunk[:room]
            if len(chunk) > room:
                self.truncated = True

    def finish(self) -> str:
        self._thread.join(timeout=5)
        return self.data.decode("utf-8", "replace")


class CliContainerRuntime:
    def __init__(self, binary: str = "podman"):
        if binary not in ("podman", "docker"):
            raise ValueError("runtime must be podman or docker")
        self.binary = binary

    def _env(self) -> dict[str, str]:
        return {k: os.environ[k] for k in HOST_ENV_KEYS if k in os.environ}

    def _exe(self) -> str | None:
        return shutil.which(self.binary, path=os.environ.get("PATH"))

    def available(self) -> bool:
        exe = self._exe()
        if exe is None:
            return False
        try:
            done = subprocess.run([exe, "info"], capture_output=True, timeout=20, env=self._env(), check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0

    def command(self, name: str, workspace: Path, argv: tuple[str, ...], policy: SandboxPolicy) -> list[str]:
        env = [part for key, value in CONTAINER_ENV.items() for part in ("--env", f"{key}={value}")]
        return [
            self._exe() or self.binary, "run", "--rm", "--name", name, "--pull", "never",
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", SANDBOX_USER, "--pids-limit", str(policy.pids),
            "--memory", f"{policy.memory_mb}m", "--memory-swap", f"{policy.memory_mb}m", "--cpus", str(policy.cpus),
            "--tmpfs", "/tmp:rw,nosuid,size=256m", "--volume", f"{workspace}:/work:rw", "--workdir", "/work",
            *env, policy.image, *argv,
        ]

    def run(self, workspace: Path, argv: tuple[str, ...], policy: SandboxPolicy) -> RawRun:
        name = f"siamang-{uuid.uuid4().hex[:16]}"
        started = time.monotonic()
        try:
            proc = subprocess.Popen(self.command(name, workspace, argv, policy), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=self._env())
        except OSError:
            return RawRun(None, "the container engine could not be started", seconds=0.0)
        reader = _Bounded(proc.stdout, policy.output_bytes)
        timed_out = False
        try:
            proc.wait(timeout=policy.timeout_secs)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._stop(name)
            proc.kill()
            proc.wait()
        output = reader.finish()
        return RawRun(None if timed_out else proc.returncode, output, timed_out, reader.truncated,
                      round(time.monotonic() - started, 2))

    def _stop(self, name: str) -> None:
        exe = self._exe()
        if exe:
            subprocess.run([exe, "rm", "--force", name], capture_output=True, timeout=30, env=self._env(), check=False)
