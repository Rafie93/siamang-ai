"""The Git execution boundary: the only place in Siamang that starts Git.

Git is treated as an untrusted external executable:

* **Argument lists only**, ``shell=False``. Nothing is ever joined into a
  command string, so path and metadata values stay data.
* **Read-only allowlist.** The first argument must be one of
  :data:`READ_ONLY_COMMANDS`, and each command has its own argument rule
  (``worktree`` only ``list``, ``remote`` only ``-v``, ``diff`` never
  ``--output``/``--ext-diff``/``--textconv``, ...). ``push``, ``pull``,
  ``fetch``, ``commit``, ``reset``, ``clean``, ``stash``, ``merge``,
  ``rebase``, ``checkout``, ``branch``, and ``worktree add`` cannot be run
  through this client at all.
* **Controlled environment.** The child gets a fixed environment, never the
  caller's: no ``HOME``, credentials, tokens, ``GIT_DIR``/``GIT_WORK_TREE``,
  or SSH agent. User and system Git config are ignored
  (``GIT_CONFIG_GLOBAL=/dev/null``, ``GIT_CONFIG_NOSYSTEM=1``) so output is
  deterministic; terminal prompts, optional index locks, and lazy object
  fetches are off.
* **Defense in depth** (``-c`` options): hooks, fsmonitor, credential
  helpers, and every transport protocol (``protocol.allow=never``) are
  disabled; ``diff`` always runs ``--no-ext-diff --no-textconv``.
* **Bounded:** every call has a timeout (default
  :data:`DEFAULT_TIMEOUT_SECS`); output beyond ``max_output`` bytes is
  truncated and flagged.
* **Normalized errors:** see :mod:`siamang.git.errors`.

Logs carry the Git subcommand, exit status, and duration only, never the
arguments, paths, or environment.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from siamang.git.errors import (
    GitError,
    GitNotInstalledError,
    GitPermissionError,
    GitTimeoutError,
    InvalidGitArgumentError,
    UnexpectedGitError,
    UnsupportedGitVersionError,
    classify_failure,
)
from siamang.log import get_logger

log = get_logger("git")

DEFAULT_TIMEOUT_SECS = 10.0
MAX_TIMEOUT_SECS = 120.0
DEFAULT_MAX_OUTPUT = 8 * 1024 * 1024
# 2.36: `worktree list -z`. (`GIT_CONFIG_GLOBAL` needs 2.32, `--path-format` 2.31.)
MIN_GIT_VERSION = (2, 36)

GLOBAL_OPTIONS: tuple[str, ...] = (
    "--no-pager",
    "-c", "core.fsmonitor=false",
    "-c", f"core.hooksPath={os.devnull}",
    "-c", "core.quotePath=false",
    "-c", "color.ui=false",
    "-c", "credential.helper=",
    "-c", "protocol.allow=never",
    "-c", "submodule.recurse=false",
)

CHILD_ENV: dict[str, str] = {
    "LC_ALL": "C",
    "LANG": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
}

_VERSION_RE = re.compile(r"^git version (\d+)\.(\d+)")


def _any(args: Sequence[str]) -> bool:
    return True


def _diff(args: Sequence[str]) -> bool:
    banned = ("--output", "--ext-diff", "--textconv", "--no-index")
    return not any(a.startswith(banned) for a in args[1:])


def _symbolic_ref(args: Sequence[str]) -> bool:
    # Reading only: one positional (`HEAD`). Two positionals would *write* the ref.
    positional = [a for a in args[1:] if not a.startswith("-")]
    flags = {a for a in args[1:] if a.startswith("-")}
    return positional == ["HEAD"] and flags <= {"--quiet", "-q", "--short"}


READ_ONLY_COMMANDS: dict[str, Callable[[Sequence[str]], bool]] = {
    "version": lambda a: len(a) == 1,
    "rev-parse": _any,
    "symbolic-ref": _symbolic_ref,
    "status": _any,
    "diff": _diff,
    "worktree": lambda a: len(a) >= 2 and a[1] == "list",
    "remote": lambda a: list(a) in (["remote"], ["remote", "-v"]),
}


@dataclass(frozen=True)
class GitResult:
    command: str  # subcommand only, e.g. "status"
    exit_code: int
    stdout: bytes
    stderr: str
    duration_ms: int
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def text(self) -> str:
        """stdout as ``str``; undecodable bytes survive as surrogate escapes (like ``os.fsdecode``)."""
        return os.fsdecode(self.stdout)


def validate_path(path: str, *, what: str = "path") -> str:
    """An absolute path string without control characters. Touches no filesystem."""
    if not isinstance(path, str) or not path:
        raise InvalidGitArgumentError(f"{what} must be a non-empty string")
    if any(unicodedata.category(c) == "Cc" for c in path):
        raise InvalidGitArgumentError(f"{what} must not contain control characters")
    if not path.startswith("/"):
        raise InvalidGitArgumentError(f"{what} must be absolute")
    return path


def validate_args(args: Sequence[str]) -> list[str]:
    """Check a structured argument list against the read-only allowlist."""
    if isinstance(args, (str, bytes)) or not isinstance(args, Sequence) or not args:
        raise InvalidGitArgumentError("git arguments must be a non-empty list of strings")
    out = list(args)
    for a in out:
        if not isinstance(a, str):
            raise InvalidGitArgumentError("git arguments must be strings")
        if "\x00" in a:
            raise InvalidGitArgumentError("git arguments must not contain NUL")
    rule = READ_ONLY_COMMANDS.get(out[0])
    if rule is None:
        raise InvalidGitArgumentError(f"git command {out[0]!r} is not allowed (read-only allowlist)")
    if not rule(out):
        raise InvalidGitArgumentError(f"arguments not allowed for git {out[0]}")
    return out


def parse_version(text: str) -> tuple[int, int]:
    m = _VERSION_RE.match(text.strip())
    if not m:
        raise UnsupportedGitVersionError("cannot determine the Git version", operation="version")
    return int(m.group(1)), int(m.group(2))


class GitClient:
    """Runs allowlisted, read-only Git commands. See the module docstring.

    ``binary`` is for trusted callers (tests, Siamang code); it is never taken
    from project metadata or configuration. By default ``git`` is located on
    ``PATH`` once, at first use.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECS,
        max_output: int = DEFAULT_MAX_OUTPUT,
    ):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT_SECS:
            raise InvalidGitArgumentError(f"timeout must be in (0, {MAX_TIMEOUT_SECS}] seconds")
        if binary is not None:
            validate_path(binary, what="git binary")
        self._binary = binary
        self.timeout = float(timeout)
        self.max_output = max_output
        self._version: tuple[int, int] | None = None

    # -- public --------------------------------------------------------------------

    @property
    def binary(self) -> str:
        if self._binary is None:
            found = shutil.which("git")
            if found is None:
                raise GitNotInstalledError("git executable not found on PATH", operation="locate")
            self._binary = found
        return self._binary

    def version(self) -> tuple[int, int]:
        """``(major, minor)`` of the Git executable. Cached."""
        if self._version is None:
            result = self._execute(["version"], cwd=None, operation="version", timeout=self.timeout,
                                   max_output=4096)
            if not result.ok:
                raise UnexpectedGitError("git version failed", operation="version", detail=result.stderr)
            self._version = parse_version(result.text())
        return self._version

    def ensure_supported(self) -> tuple[int, int]:
        version = self.version()
        if version < MIN_GIT_VERSION:
            need = ".".join(map(str, MIN_GIT_VERSION))
            have = ".".join(map(str, version))
            raise UnsupportedGitVersionError(f"Git {have} is too old; Siamang needs Git >= {need}", operation="version")
        return version

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str,
        operation: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> GitResult:
        """Run ``git -C <cwd> <args>``. Raises :class:`GitError` on failure when ``check``."""
        argv = validate_args(args)
        cwd = validate_path(cwd, what="repository path")
        if timeout is not None and not 0 < timeout <= MAX_TIMEOUT_SECS:
            raise InvalidGitArgumentError(f"timeout must be in (0, {MAX_TIMEOUT_SECS}] seconds")
        self.ensure_supported()
        if argv[0] == "diff":
            argv = [argv[0], "--no-ext-diff", "--no-textconv", "--no-color", *argv[1:]]
        op = operation or argv[0]
        result = self._execute(argv, cwd=cwd, operation=op, timeout=timeout or self.timeout,
                               max_output=self.max_output)
        if check and not result.ok:
            raise classify_failure(result.stderr, operation=op, repository_path=cwd, exit_code=result.exit_code)
        return result

    # -- internals -----------------------------------------------------------------

    def _environment(self, binary: str) -> dict[str, str]:
        path = os.pathsep.join(dict.fromkeys([os.path.dirname(binary), "/usr/bin", "/bin"]))
        return {"PATH": path, **CHILD_ENV}

    def _execute(
        self, argv: list[str], *, cwd: str | None, operation: str, timeout: float, max_output: int
    ) -> GitResult:
        binary = self.binary
        full = [binary, *GLOBAL_OPTIONS, *(["-C", cwd] if cwd is not None else []), *argv]
        started = time.monotonic()
        failure: GitError | None = None
        proc = None
        try:
            proc = subprocess.run(
                full,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=self._environment(binary),
                cwd="/",  # never the caller's working directory
                timeout=timeout,
                check=False,
                close_fds=True,
            )
        except FileNotFoundError:
            failure = GitNotInstalledError("git executable not found", operation=operation)
        except PermissionError:
            failure = GitPermissionError("git executable is not runnable", operation=operation)
        except subprocess.TimeoutExpired:
            failure = GitTimeoutError(f"git {argv[0]} timed out after {timeout:g}s", operation=operation,
                                      repository_path=cwd)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            failure = UnexpectedGitError(f"could not run git ({type(exc).__name__})", operation=operation)
        ms = int((time.monotonic() - started) * 1000)
        if failure is not None:  # raised outside `except`: no raw exception chained
            log.warning("git %s failed: %s (%d ms)", argv[0], failure.code, ms)
            raise failure
        assert proc is not None
        stdout = proc.stdout
        truncated = len(stdout) > max_output
        if truncated:
            stdout = stdout[:max_output]
        log.debug("git %s exit=%d (%d ms)", argv[0], proc.returncode, ms)
        return GitResult(
            command=argv[0],
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=os.fsdecode(proc.stderr),
            duration_ms=ms,
            truncated=truncated,
        )
