"""Database configuration validation without database or provider imports."""

from urllib.parse import urlsplit


def test_database_url(environ):
    """Require an explicit local PostgreSQL target for test mode.

    Query parameters are prohibited because driver options can override the host.
    Never include the supplied URL in errors: it may contain credentials.
    """
    url = environ.get("TEST_DATABASE_URL")
    if not url:
        raise ValueError("APP_ENV=test requires TEST_DATABASE_URL")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "postgresql+asyncpg"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and parsed.path == "/listing_alerts_test"
            and not parsed.query
            and not parsed.fragment
            and parsed.port != 0
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "TEST_DATABASE_URL must use postgresql+asyncpg, a loopback host, "
            "and database listing_alerts_test, with no query or fragment"
        )
    return url
