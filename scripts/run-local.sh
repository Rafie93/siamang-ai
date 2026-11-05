#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
credentials="${HOME}/.config/siamang/local.env"

if [[ ! -f "$credentials" ]]; then
    echo "Local credentials not found. Create them first: python3 scripts/init-credentials.py" >&2
    exit 1
fi

source "$credentials"
export SIAMANG_CONFIG="$repo_root/siamang.toml"
cd "$repo_root"
exec "$repo_root/.venv/bin/python" -m siamang.api.main "$@"
