#!/usr/bin/env python3
"""Create ~/.config/siamang/local.env once: a local operator identity and the evidence seal key.

Refuses to overwrite an existing file, because replacing the seal key makes existing evidence unusable.
Print the token once; the server only ever stores its hash.
"""

import hashlib
import json
import secrets
import shlex
import sys
from pathlib import Path

PERMISSIONS = ["read", "submit", "run", "cancel", "retry", "approve", "configure"]


def main() -> int:
    path = Path.home() / ".config" / "siamang" / "local.env"
    if path.exists():
        print(f"{path} already exists; leaving it untouched.", file=sys.stderr)
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    identities = {
        "owner": {
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "permissions": PERMISSIONS,
            "projects": ["*"],
        }
    }
    path.touch(mode=0o600, exist_ok=False)
    path.write_text(
        f"export SIAMANG_API_IDENTITIES={shlex.quote(json.dumps(identities))}\n"
        f"export SIAMANG_API_SEAL_KEY={secrets.token_hex(32)}\n"
    )
    print(f"export SIAMANG_TOKEN={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
