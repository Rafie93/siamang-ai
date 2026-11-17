"""Accounts the owner connects from Settings: GitHub (a token kept by Siamang) and Claude (the shared CLI login).

No token or credential is ever returned, logged, or written to siamang.toml or the database.
"""
from .claude import ClaudeAccount
from .errors import AccountError
from .github import GitHubAccount

__all__ = ['AccountError', 'ClaudeAccount', 'GitHubAccount']
