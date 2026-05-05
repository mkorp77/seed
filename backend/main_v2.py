from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, sessionmaker

from seed_api import router
from seed_db_v2 import (
    build_engine,
    build_session_factory,
    get_allowed_origins,
    initialize_database,
    should_initialize_database,
)


def _build_session_factory() -> sessionmaker[Session]:
    database_url = os.getenv("DATABASE_URL")
    engine = build_engine(database_url=database_url, echo=os.getenv("SEED_SQL_ECHO", "false").lower() == "true")

    if should_initialize_database():
        initialize_database(engine)

    return build_session_factory(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.session_factory = _build_session_factory()
    yield


app = FastAPI(
    title="Seed API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", response_class=FileResponse)
async def capture_page():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "capture.html"),
        media_type="text/html",
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        "main_v2:app",
        host=os.getenv("SEED_HOST", "127.0.0.1"),
        port=int(os.getenv("SEED_PORT", "8000")),
        reload=os.getenv("SEED_RELOAD", "false").lower() == "true",
    )
