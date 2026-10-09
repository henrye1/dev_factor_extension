"""FastAPI application factory.

Run locally:  uvicorn hazard_ext.web.main:create_app --factory --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .db import init_db, make_engine, make_session_factory
from .models import User
from .routers import agent, auth, exports, projects, scenarios
from .runner import DataCache, migrate_three_methods
from .security import COOKIE_NAME, LoginThrottle, SessionSigner, hash_password, password_problem
from .storage import make_blob_store

log = logging.getLogger("hazard_ext")
STATIC = Path(__file__).parent / "static"


def bootstrap_admin(session_factory, settings: Settings) -> None:
    """Create the first administrator when the user table is empty."""
    with session_factory() as db:
        if db.execute(select(func.count()).select_from(User)).scalar_one() > 0:
            return
        if not (settings.admin_email and settings.admin_password):
            log.warning("No users exist. Set ADMIN_EMAIL and ADMIN_PASSWORD and restart to create the first administrator")
            return
        problem = password_problem(settings.admin_password)
        if problem:
            raise RuntimeError(f"ADMIN_PASSWORD: {problem}")
        db.add(User(email=settings.admin_email.strip().lower(), name=settings.admin_name,
                    password_hash=hash_password(settings.admin_password), is_admin=True))
        db.commit()
        log.info("Created the first administrator %s", settings.admin_email)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    fatal = settings.fatal_problems()
    if fatal:
        raise RuntimeError("Configuration error: " + "; ".join(fatal))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for problem in settings.problems():
            log.warning("Configuration: %s", problem)
        if settings.auto_create_schema:
            init_db(app.state.engine, settings)
        bootstrap_admin(app.state.session_factory, settings)
        migrate_three_methods(app.state.session_factory)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="LGD Tail Extension", lifespan=lifespan, redoc_url=None,
                  docs_url="/api/docs" if settings.api_docs else None,
                  openapi_url="/api/openapi.json" if settings.api_docs else None)
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    store = make_blob_store(settings, session_factory)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.store = store
    app.state.cache = DataCache(store)
    app.state.signer = SessionSigner(settings.session_secret, settings.session_hours * 3600)
    app.state.throttle = LoginThrottle(max_failures=8)            # per account
    app.state.addr_throttle = LoginThrottle(max_failures=40)      # per address

    for module in (auth, projects, scenarios, exports, agent):
        app.include_router(module.router)
    app.state.agent_client = None
    if settings.anthropic_api_key:
        import anthropic                          # only needed when the assistant is switched on
        app.state.agent_client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2, timeout=120.0)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/features")
    def features():
        return {"assistant": app.state.agent_client is not None}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method == "POST" and request.url.path.endswith(("/datasets", "/curves")):
            # refuse an anonymous or oversized upload before its body is read
            if app.state.signer.read(request.cookies.get(COOKIE_NAME)) is None:
                return JSONResponse({"detail": "Not signed in"}, status_code=401)
            length = request.headers.get("content-length", "")
            limit = (settings.max_upload_mb + 1) * 1024 * 1024
            if not length.isdigit():
                return JSONResponse({"detail": "The upload needs a Content-Length header"}, status_code=411)
            if int(length) > limit:
                return JSONResponse({"detail": f"Uploads are limited to {settings.max_upload_mb} MB per request"},
                                    status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.url.path.endswith((".js", ".css", ".html")) or request.url.path == "/":
            # revalidate every time (the ETag makes that cheap), so a new release is picked up on reload
            response.headers.setdefault("Cache-Control", "no-cache")
        if not request.url.path.startswith("/api/docs"):
            # the front end loads only its own scripts, styles and fonts
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        return response

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        # two requests raced for the same unique name, or a value did not fit its column
        return JSONResponse({"detail": "That conflicts with something saved a moment ago. Reload and try again"},
                            status_code=409)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Something went wrong on the server"}, status_code=500)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app

