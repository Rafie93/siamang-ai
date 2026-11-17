"""Refusals from connecting an account, each with the HTTP status the transport answers with."""


class AccountError(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.code, self.status = code, status
