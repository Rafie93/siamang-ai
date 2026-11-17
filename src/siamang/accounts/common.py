BASE_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR')
STATUS_TIMEOUT = 15


def text(value, limit=200):
    return value[:limit] if isinstance(value, str) and value else None
