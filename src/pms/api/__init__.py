"""The HTTP API the portals use (docs/adr/0002-portals-and-exports.md). Run it with
`python -m pms.api`; `create_app` builds it for tests and for uvicorn."""

from pms.api.app import Settings, create_app

__all__ = ["Settings", "create_app"]
