"""FastAPI app: webhook intake, health, and (when an identity + service context are supplied) the portal routes."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from . import routes, webhooks
from .config import Settings
from .db import make_engine, make_session_factory


def create_app(settings: Settings | None = None, session_factory=None, *, identity=None, ctx=None) -> FastAPI:
    settings = settings or Settings()
    session_factory = session_factory or make_session_factory(make_engine())
    app = FastAPI(title="Labeling API", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        with session_factory() as s:
            s.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.post("/api/v1/labeling/webhooks/cvat", status_code=202)
    async def cvat_webhook(request: Request):
        raw = await request.body()  # signature is over the exact bytes received
        with session_factory() as s:
            try:
                outcome = webhooks.receive(s, settings, raw, request.headers.get("x-signature-256"))
            except webhooks.BadSignature:
                raise HTTPException(status_code=401, detail={"error": "invalid signature", "statusCode": 401, "code": "invalid_signature"}) from None
        return {"status": outcome}

    if identity is not None and ctx is not None:
        app.include_router(routes.build_router(routes.Deps(settings, session_factory, identity, ctx)))

        @app.exception_handler(routes.ApiError)
        async def _api_error(request: Request, exc: routes.ApiError):
            return routes.error_response(request, exc)

    return app
