"""Credential handling.

Config holds only a *reference* (the name of an environment variable,
``api_key_env``). The value is resolved at provider construction into a
:class:`Secret`, which never renders its value in ``str``/``repr``/logs and
cannot be pickled or copied into metadata by accident.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        self._value = value

    def reveal(self) -> str:
        """The only way to read the value. Call it at the transport boundary, nowhere else."""
        return self._value

    def __repr__(self) -> str:
        return "Secret('********')"

    __str__ = __repr__

    def __reduce__(self):  # pickling would serialize the value
        raise TypeError("Secret cannot be pickled")

    def __eq__(self, other: object) -> bool:
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]


def resolve_credential(env_name: str | None, env: Mapping[str, str]) -> Secret | None:
    """Look up ``env_name``; ``None`` if no reference is configured or the variable is unset/empty."""
    if not env_name:
        return None
    if not ENV_NAME_RE.match(env_name):
        raise ValueError(f"invalid environment variable name {env_name!r}")
    value = env.get(env_name)
    return Secret(value) if value else None
