"""Generate configuration locally. Never loads .env or contacts a service."""
import getpass
import secrets

from app.admin_auth import hash_password

if __name__ == "__main__":
    password = getpass.getpass("New admin password (at least 12 characters): ")
    confirm = getpass.getpass("Confirm password: ")
    if len(password) < 12 or password != confirm:
        raise SystemExit("Passwords must match and contain at least 12 characters")
    print("Copy these values into the backend environment; do not commit them:")
    print("ADMIN_USERNAME=admin")
    print("ADMIN_PASSWORD_HASH='" + hash_password(password) + "'")
    print("ADMIN_SESSION_SECRET=" + secrets.token_urlsafe(48))
