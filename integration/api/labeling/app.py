"""FastAPI app (Phase 1 surface): webhook intake + health. Remaining labeling routes arrive in Phase 2."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from . import webhooks
from .config import Settings
from .db import make_engine, make_session_factory


def create_app(settings: Settings | None = None, session_factory=None) -> FastAPI:
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

    return app
