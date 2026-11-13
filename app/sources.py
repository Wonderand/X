from urllib.parse import urlsplit, urlunsplit
from app.errors import DomainError


def canonical_url(value):
    try:
        parts = urlsplit(value.strip())
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        if len(value) > 2000:
            raise ValueError()
        return urlunsplit(('https', parts.netloc.lower(), parts.path or '/', parts.query, ''))
    except ValueError:
        raise DomainError('请输入有效公开 HTTPS 来源链接') from None
