"""Create a private preview config, using the existing local DB URL but no email keys."""
import argparse
import getpass
import os
import secrets
import shlex
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from app.admin_auth import hash_password
from app.preview import PREVIEW_FILE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tunnel', action='store_true', help='Use an existing SSH tunnel at 127.0.0.1:55433')
    parser.add_argument('--ssh-host', help='SSH login for the production server; defaults to root at the configured DB host')
    args = parser.parse_args()
    if PREVIEW_FILE.exists():
        raise SystemExit('Preview configuration already exists. Edit .env.preview.local privately to change it.')
    settings = dotenv_values(Path(__file__).resolve().parent.parent / '.env', interpolate=False)
    try:
        url = make_url(settings.get('DATABASE_URL') or '')
        if url.drivername != 'postgresql+asyncpg' or not url.host or not url.database:
            raise ValueError()
    except Exception:
        raise SystemExit('No valid database connection found in the existing local .env.') from None
    tunnel_command = None
    if args.tunnel:
        tunnel_command = ['ssh', '-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30',
                          '-L', f'127.0.0.1:55433:127.0.0.1:{url.port or 5432}', args.ssh_host or f'root@{url.host}']
        url = url.set(host='127.0.0.1', port=55433)
    print('Preparing read-only access using the existing local database connection.')
    print('Choose a LOCAL preview admin password. It does not change production login.')
    password = getpass.getpass('Local preview password (at least 12 characters): ')
    if len(password) < 12 or password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords must match and contain at least 12 characters.')
    values = {'PREVIEW_DATABASE_URL': url.render_as_string(hide_password=False),
              'ADMIN_USERNAME': 'admin', 'ADMIN_PASSWORD_HASH': hash_password(password),
              'ADMIN_SESSION_SECRET': secrets.token_urlsafe(48)}
    # Exclusive creation avoids accidental overwrites. Restrictive permissions
    # apply immediately, including while credentials are being written.
    fd = os.open(PREVIEW_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write('# Local read-only preview; never commit this file.\n')
        for key, value in values.items():
            escaped = value.replace('\\', '\\\\').replace("'", "\\'")
            file.write(f"{key}='{escaped}'\n")
    print('Saved .env.preview.local with owner-only permissions. Username: admin.')
    print('No database was contacted and no production settings were changed.')
    if tunnel_command:
        print('Open a separate terminal and leave this encrypted SSH tunnel running:')
        print(shlex.join(tunnel_command))


if __name__ == '__main__':
    main()
