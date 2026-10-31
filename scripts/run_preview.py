"""Launch the local read-only production-data preview on loopback port 8000."""
import os
from pathlib import Path


def main():
    os.environ['APP_ENV'] = 'preview'
    from app.preview import preview_database_url
    preview_database_url()  # Fail before startup if explicit config is missing.
    os.chdir(Path(__file__).resolve().parent.parent)
    import uvicorn
    print('LIVE DATA PREVIEW: database read-only; email, webhooks, subscriptions and sync disabled.')
    uvicorn.run('app.main:app', host='127.0.0.1', port=8000, proxy_headers=False)


if __name__ == '__main__':
    main()
