"""Server-owned identities and scopes. No authority is accepted from requests."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PERMISSIONS = frozenset({'read', 'submit', 'run', 'cancel', 'retry', 'approve', 'configure'})


@dataclass(frozen=True)
class Principal:
    identity: str
    token_sha256: str = field(repr=False)
    permissions: frozenset[str] = frozenset()
    projects: frozenset[str] = frozenset()

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', self.identity):
            raise ValueError('invalid API identity')
        if not re.fullmatch(r'[0-9a-f]{64}', self.token_sha256):
            raise ValueError('token_sha256 must be a SHA256 digest')
        object.__setattr__(self, 'permissions', frozenset(self.permissions))
        object.__setattr__(self, 'projects', frozenset(self.projects))
        if not self.permissions <= PERMISSIONS:
            raise ValueError('unknown API permission')
        if any(p != '*' and not re.fullmatch(r'prj_[A-Za-z0-9]{1,64}', p) for p in self.projects):
            raise ValueError('invalid API project scope')

    def allows_project(self, project_id):
        return '*' in self.projects or project_id in self.projects


@dataclass(frozen=True)
class APISettings:
    database: Path
    principals: tuple[Principal, ...]
    seal_key: bytes = field(repr=False)
    config: str | None = None
    origins: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'database', Path(self.database).expanduser().resolve())
        object.__setattr__(self, 'principals', tuple(self.principals))
        object.__setattr__(self, 'origins', tuple(self.origins))
        if not self.principals or len(self.seal_key) < 32:
            raise ValueError('API requires identities and a persistent seal key of at least 32 bytes')
        if len({p.identity for p in self.principals}) != len(self.principals):
            raise ValueError('duplicate API identity')
        if len({p.token_sha256 for p in self.principals}) != len(self.principals):
            raise ValueError('shared API credentials are not allowed')
        if any(o == '*' or not re.fullmatch(r'https?://[^/]+', o) for o in self.origins):
            raise ValueError('CORS requires explicit HTTP origins')

    def authenticate(self, token: str) -> Principal | None:
        if not 32 <= len(token) <= 512:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        found = None
        for principal in self.principals:
            if hmac.compare_digest(digest, principal.token_sha256):
                found = principal
        return found

    @classmethod
    def from_env(cls):
        try:
            from siamang.config import load_config
            config_path = os.environ.get('SIAMANG_CONFIG')
            config = load_config(config_path)
            identities = json.loads(os.environ['SIAMANG_API_IDENTITIES'])
            principals = tuple(Principal(identity=name, **value) for name, value in identities.items())
            key = bytes.fromhex(os.environ['SIAMANG_API_SEAL_KEY'])
            origins = tuple(json.loads(os.environ.get('SIAMANG_API_ORIGINS', '[]')))
            return cls(Path(config.storage.path), principals, key, config_path, origins)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError('invalid API configuration; see docs/HTTP_API.md') from None
