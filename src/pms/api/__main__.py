"""Serve the API: `python -m pms.api`. Settings come from the environment: DATABASE_URL,
PMS_PUBLIC_URL, PMS_COOKIE_SECURE, PMS_DB_ROLE, PMS_NIGHTLY ("off" turns the nightly rent run off),
and PMS_HOST / PMS_PORT to listen on."""

import os

import uvicorn

from pms.api import Settings, create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(Settings.from_env()),
        host=os.environ.get("PMS_HOST", "127.0.0.1"),
        port=int(os.environ.get("PMS_PORT", "8000")),
        proxy_headers=False,
    )
