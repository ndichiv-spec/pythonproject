"""Scratch measurement for the session tile door and /maps registration. Deleted when done."""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + (tempfile.mkdtemp() + "/probe.db").replace(os.sep, "/")
os.environ["JARVIS_ENV"] = "test"
os.environ["JARVIS_GEO_ENABLED"] = ""

import app.main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

served = TestClient(app.main.app, raise_server_exceptions=False)
paths = [getattr(r, "path", "") for r in app.main.app.routes]
print("MAPS in routes:", "/maps" in paths)
print("tile in routes:", "/geo/tile/{source}/{z}/{x}/{y}" in paths)
print("interesting:", [p for p in paths if p in ("/maps", "/media", "/geo/tile/{source}/{z}/{x}/{y}")])
print("mounted:", [(getattr(r, "path", ""), type(r).__name__) for r in app.main.app.routes
                   if getattr(r, "routes", None)])
response = served.get("/geo/tile/osm/1/0/0")
print("GET tile ->", response.status_code,
      dict(response.headers).get("x-jarvis-retrieval-state"), response.text[:200])
page = served.get("/maps")
print("GET /maps ->", page.status_code, len(page.text))
