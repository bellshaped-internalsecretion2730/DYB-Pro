"""Foldsmith API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.db import init_db, session_scope

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

DESCRIPTION = """\
Foldsmith is a pre-wetlab protein design OS. A scientist states a goal, drops sequences or
structures, and an autonomous Devin agent swarm runs in-silico design cycles. Every design is an
immutable commit in a version graph, and every cycle ends in a ranked, orderable wet-lab shortlist
with cost and risk against testing everything.

Auth: send `X-API-Key`. Roles: `viewer` (read), `scientist` (run cycles), `admin`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session_scope() as db:
        from app.seed import seed_all

        seed_all(db)
    logger.info("foldsmith api ready (env=%s)", settings.app_env)
    yield


app = FastAPI(
    title="Foldsmith API",
    version="0.1.0",
    description=DESCRIPTION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "foldsmith", "docs": "/docs", "api": "/api"}
