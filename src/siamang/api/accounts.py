"""The accounts Settings shows; the process work lives in :mod:`siamang.accounts`."""
from siamang.accounts import ClaudeAccount, GitHubAccount


class Accounts:
    def __init__(self, github=None, claude=None):
        self.github = github or GitHubAccount()
        self.claude = claude or ClaudeAccount()

    def close(self):
        self.claude.close()
