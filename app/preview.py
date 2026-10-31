"""Explicit local preview of live data. No implicit production configuration fallback."""
import ipaddress
import os
from pathlib import Path

from dotenv import dotenv_values

PREVIEW_FILE = Path(__file__).resolve().parent.parent / '.env.preview.local'
PROVIDER_KEYS = {'RESEND_API_KEY', 'RESEND_WEBHOOK_SECRET', 'RMLS_TOKEN',
                 'EMAIL_USER', 'EMAIL_PASSWORD', 'ALERT_EMAIL', 'BREVO_API_KEY'}
LOCAL_ORIGINS = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (3000, 3001)}


def is_preview():
    return os.getenv('APP_ENV', '').strip().lower() == 'preview'


def require_writable_mode():
    if is_preview():
        raise RuntimeError('This operation is disabled in read-only preview mode')


def preview_database_url():
    # Copy only the intended settings. Never import provider keys or APP_ENV.
    settings = dotenv_values(PREVIEW_FILE, interpolate=False) if PREVIEW_FILE.exists() else {}
    for key in ('PREVIEW_DATABASE_URL', 'ADMIN_USERNAME', 'ADMIN_PASSWORD_HASH', 'ADMIN_SESSION_SECRET'):
        if settings.get(key):
            os.environ[key] = settings[key]
    for key in PROVIDER_KEYS:
        os.environ.pop(key, None)
    os.environ['ADMIN_ALLOWED_ORIGINS'] = ','.join(sorted(LOCAL_ORIGINS))
    value = os.getenv('PREVIEW_DATABASE_URL')
    if not value:
        raise ValueError('Preview requires PREVIEW_DATABASE_URL; run python -m scripts.configure_preview')
    from sqlalchemy.engine import make_url
    try:
        url = make_url(value)
        if url.drivername != 'postgresql+asyncpg' or not url.host or not url.database:
            raise ValueError()
    except Exception:
        raise ValueError('PREVIEW_DATABASE_URL must be a PostgreSQL asyncpg connection URL') from None
    return value


def connection_options(url):
    from sqlalchemy.engine import make_url
    import ssl
    target = make_url(url)
    options = {'timeout': 10, 'server_settings': {
        'default_transaction_read_only': 'on', 'statement_timeout': '15000',
        'application_name': 'realty_local_readonly_preview',
    }}
    # A loopback target can be an encrypted SSH tunnel; remote targets require
    # certificate-verified TLS. No insecure TLS fallback is performed.
    if target.host not in {'localhost', '127.0.0.1', '::1'}:
        options['ssl'] = ssl.create_default_context()
    return options


async def preview_guard(request, call_next):
    if not is_preview():
        return await call_next(request)
    from starlette.responses import JSONResponse
    try:
        local_peer = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local_peer = False
    origin = request.headers.get('origin', '').rstrip('/')
    if (not local_peer or request.url.hostname not in {'localhost', '127.0.0.1', '::1'}
            or (origin and origin not in LOCAL_ORIGINS)):
        return JSONResponse({'detail': 'Preview is available only on this computer'}, status_code=403,
                            headers={'Cache-Control': 'no-store'})
    path = request.url.path
    read_path = path in {'/api/health', '/api/listings', '/api/admin/session', '/api/admin/activity', '/map', '/docs',
                        '/redoc', '/openapi.json', '/docs/oauth2-redirect'} or path.startswith(
                            ('/api/listings/', '/api/market/', '/api/admin/clients', '/p/', '/static/'))
    allowed = (request.method == 'OPTIONS' or (request.method in {'GET', 'HEAD'} and read_path)
               or (request.method == 'POST' and path in {'/api/admin/login', '/api/admin/logout'}))
    if not allowed:
        return JSONResponse({'detail': 'Read-only preview: this action is disabled'}, status_code=403,
                            headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Data-Mode'] = 'production-readonly-preview'
    return response
