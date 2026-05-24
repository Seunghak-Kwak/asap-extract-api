from pathlib import Path

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import FileResponse, PlainTextResponse, Response

from app.api.admin import router as admin_router
from app.api.v1 import router as v1_router
from app.observability.logging import configure as configure_logging
from app.observability.middleware import RequestIdMiddleware


def build_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Extract API", version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    app.include_router(v1_router)
    app.include_router(admin_router)

    @app.get("/healthz")
    async def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    admin_html = Path(__file__).parent / "static" / "admin.html"

    @app.get("/admin", include_in_schema=False)
    async def admin_panel() -> FileResponse:
        return FileResponse(admin_html, media_type="text/html")

    return app


app = build_app()
