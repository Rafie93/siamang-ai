"""The risk of a change, judged from what it really touches. No model has a say.

Two moments, on purpose. Before any code exists only the owner's brief and the planned paths are known, so that
assessment is a forecast. The patch is assessed again from its own paths and added lines, and that one decides whether
code leaves the building. Model-written design documents are not read: they mention security in almost every task, and
a document that stays silent proves nothing.

These are heuristics and they lean one way: a false alarm costs one human approval, a miss costs an unreviewed change.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

REASONS_LIMIT = 12
MAX_LOW_FILES = 20
MAX_LOW_LINES = 600
MAX_LOW_DELETES = 5
MAX_LOW_PACKAGES = 4


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_ORDER = (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)


@dataclass(frozen=True)
class Risk:
    level: RiskLevel = RiskLevel.LOW
    reasons: tuple[str, ...] = ()

    def join(self, *others: Risk) -> Risk:
        every = (self, *others)
        return Risk(max((r.level for r in every), key=_ORDER.index),
                    tuple(dict.fromkeys(x for r in every for x in r.reasons))[:REASONS_LIMIT])


def _words(*names: str) -> re.Pattern[str]:
    return re.compile(rf"(^|_)({'|'.join(names)})(_|$)")


AUTH = "autentikasi atau izin"
SCHEMA = "skema database"
SENSITIVE = "keamanan atau rahasia"
DELIVERY = "CI atau deploy"
MONEY = "uang atau data pengguna"
EXECUTION = "eksekusi perintah"
BUILD = "dependensi atau build"
BROAD = "perubahan luas"

# Matched against a path reduced to its words: ``src/UserAuth.ts`` reads ``src_user_auth_ts``, ``authors.py`` has no ``auth``.
_PATHS = (
    (RiskLevel.HIGH, AUTH, _words("auth", "authn", "authz", "oauth2?", "authentication", "authenticate", "authorization",
                                  "authorize", "login", "logout", "sign_?in", "sign_?up", "sessions?", "jwt", "sso", "saml",
                                  "passwords?", "passwd", "permissions?", "rbac", "acls?")),
    (RiskLevel.HIGH, SCHEMA, _words("migrations?", "migrate", "alembic", "flyway", "liquibase", "schemas?", "ddl", "sql", "prisma")),
    (RiskLevel.HIGH, SENSITIVE, _words("credentials?", "secrets?", "tokens?", "crypto", "cryptography", "encryption", "security",
                                    "csrf", "cors", "firewall", "env", "pem", "key", "keys")),
    (RiskLevel.HIGH, DELIVERY, _words("github_workflows", "github_actions", "gitlab_ci", "circleci", "travis", "jenkinsfile",
                                      "azure_pipelines", "codeowners", "deploy", "deployment", "terraform", "tf", "k8s",
                                      "kubernetes", "helm", "ansible", "infra", "infrastructure")),
    (RiskLevel.HIGH, MONEY, _words("payments?", "billing", "invoices?", "checkout")),
    (RiskLevel.MEDIUM, BUILD, re.compile(
        r"(^|_)(package_json|package_lock_json|yarn_lock|pnpm_lock_yaml|requirements(_[a-z0-9]+)*_txt|pyproject_toml|setup_py"
        r"|setup_cfg|pipfile(_lock)?|poetry_lock|uv_lock|go_mod|go_sum|cargo_toml|cargo_lock|composer_json|composer_lock"
        r"|gemfile(_lock)?|pom_xml|build_gradle(_kts)?|dockerfile|docker_compose(_[a-z0-9]+)*|makefile|npmrc)$")),
)
# Only lines the patch adds: a file that already handles passwords does not make every edit to it risky.
_CONTENT = (
    (SCHEMA, re.compile(r"\b(create|alter|drop|truncate)\s+(table|database|schema|index)\b", re.I)),
    (AUTH, re.compile(r"\b(password|passwd|jwt|oauth2?|bearer|csrf)\b", re.I)),
    (SENSITIVE, re.compile(r"\b(api[_-]?key|private[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|hmac|bcrypt|argon2"
                        r"|pbkdf2|scrypt)\b", re.I)),
    (EXECUTION, re.compile(r"\b(eval|exec)\s*\(|\bsubprocess\b|\bos\.system\b|\bchild_process\b|\bshell_exec\b"
                           r"|\bdangerouslySetInnerHTML\b", re.I)),
)
# The owner's brief, in the two languages it is written in.
_TEXT = (
    (RiskLevel.HIGH, AUTH, re.compile(
        r"\b(auth(n|z|entication|enticate|orization|orize)?|oauth2?|login|logout|sign[ -]?(in|up)|autentikasi|otentikasi|otorisasi"
        r"|kata sandi|password|jwt|session|sesi pengguna|hak akses|izin akses|permissions?|rbac|sso)\b", re.I)),
    (RiskLevel.HIGH, SCHEMA, re.compile(
        r"\b(skema|schema|migrasi|migrations?|migrate|create table|alter table|drop table|tabel baru|kolom baru|basis data"
        r"|database)\b", re.I)),
    (RiskLevel.HIGH, SENSITIVE, re.compile(
        r"\b(keamanan|security|kerentanan|vulnerab\w*|enkripsi|encrypt\w*|kredensial|credentials?|secrets?|rahasia|tokens?)\b", re.I)),
    (RiskLevel.HIGH, DELIVERY, re.compile(r"\b(deploy\w*|produksi|production|github actions|workflow ci|pipeline ci)\b", re.I)),
    (RiskLevel.HIGH, MONEY, re.compile(r"\b(pembayaran|payments?|billing|tagihan|hapus data|delete data)\b", re.I)),
    (RiskLevel.MEDIUM, BROAD, re.compile(r"\b(upgrade|refactor\w*|dependency|dependencies|dependensi|framework)\b", re.I)),
)


def _reduced(path: str) -> str:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", path)
    return "_".join(re.findall(r"[a-z0-9]+", split.lower()))


def assess_paths(paths: Iterable[str], lines: int = 0) -> Risk:
    """Risk from names and size alone: all that is known of a plan, and of a pull request read back from GitHub."""
    paths = tuple(paths)
    found: list[Risk] = []
    for path in paths:
        reduced = _reduced(path)
        found += [Risk(level, (f"{label}: {path}",)) for level, label, rule in _PATHS if rule.search(reduced)]
    if len(paths) > MAX_LOW_FILES:
        found.append(Risk(RiskLevel.MEDIUM, (f"{BROAD}: {len(paths)} file",)))
    if lines > MAX_LOW_LINES:
        found.append(Risk(RiskLevel.MEDIUM, (f"{BROAD}: {lines} baris",)))
    return Risk().join(*found)


def assess_patch(changes: Iterable[Any], current: Callable[[str], str] = lambda path: "") -> Risk:
    """``current(path)`` gives a file's text before the patch, so only what the patch adds is read."""
    changes = tuple(changes)
    found: list[Risk] = []
    lines = deletes = 0
    for change in changes:
        deleted = str(change.operation) == "delete"
        deletes += deleted
        before, after = Counter(current(change.path).splitlines()), Counter(("" if deleted else change.after_content or "").splitlines())
        added = after - before
        lines += sum(added.values()) + sum((before - after).values())
        found += [Risk(RiskLevel.HIGH, (f"{label}: {change.path}",)) for label, rule in _CONTENT
                  if any(rule.search(line) for line in added)]
    if deletes > MAX_LOW_DELETES:
        found.append(Risk(RiskLevel.MEDIUM, (f"{BROAD}: {deletes} file dihapus",)))
    return assess_paths([c.path for c in changes], lines).join(*found)


def assess_brief(text: str, owned_paths: Iterable[str] = (), packages: int = 0) -> Risk:
    """A forecast before any code exists. It can only hold work back; the patch is judged again later."""
    found = [Risk(level, (f"{label}: disebut di brief",)) for level, label, rule in _TEXT if rule.search(text or "")]
    if packages > MAX_LOW_PACKAGES:
        found.append(Risk(RiskLevel.MEDIUM, (f"{BROAD}: {packages} paket kerja",)))
    return assess_paths(owned_paths).join(*found)
