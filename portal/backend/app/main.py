from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.routes import router
from app.config import get_settings
from app.db.models import make_session_factory
from app.domain.catalog import load_catalog
from app.domain.git_resolve import build_git_resolver
from app.domain.registry import registry_from_settings
from app.services.jenkins import configure_jenkins_client
from app.services.seed import seed_preset_images


def create_app(database_url: str | None = None, catalog_path: Path | None = None) -> FastAPI:
    settings = get_settings()
    if not settings.allow_insecure_defaults and settings.is_default_hmac_secret:
        raise RuntimeError(
            "Refusing to start with default PORTAL_CALLBACK_HMAC_SECRET; "
            "set a strong secret or PORTAL_ALLOW_INSECURE_DEFAULTS=true for local only"
        )
    db_url = database_url or settings.database_url
    cat_path = catalog_path or settings.catalog_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        catalog = load_catalog(Path(cat_path))
        session_factory = make_session_factory(db_url)
        configure_jenkins_client(
            base_url=settings.jenkins_url,
            username=settings.jenkins_username,
            api_token=settings.jenkins_api_token,
        )
        app.state.settings = settings
        app.state.catalog = catalog
        app.state.session_factory = session_factory
        app.state.git_resolver = build_git_resolver(
            mode=settings.git_resolve_mode,
            url_template=settings.git_url_template,
            http_base_url=settings.git_http_base_url,
            token=settings.git_token,
            require_exact=settings.git_require_exact,
        )
        with session_factory() as session:
            seed_preset_images(session, catalog)
            session.commit()
        yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    origins = [o.strip() for o in (settings.cors_origins or "*").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins != ["*"] else ["*"],
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/livez")
    def livez():
        """Process liveness — no DB dependency (use for K8s livenessProbe)."""
        return {"status": "alive"}

    @app.get("/healthz")
    def healthz():
        db_ok = False
        try:
            with app.state.session_factory() as session:
                session.execute(text("SELECT 1"))
                db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        reg = registry_from_settings(settings)
        status = "ok" if db_ok else "degraded"
        return {
            "status": status,
            "database": db_ok,
            "simulateWorkers": settings.simulate_workers,
            "localFactory": settings.local_factory,
            "jenkinsConfigured": bool(settings.jenkins_url and settings.jenkins_api_token),
            "insecureDefaults": settings.allow_insecure_defaults and settings.is_default_hmac_secret,
            "gitResolveMode": settings.git_resolve_mode,
            "requireAuth": settings.require_auth,
            "registryHost": reg.host,
            "registryPushHost": reg.push_host,
            "registryFinal": reg.final_image,
        }

    @app.get("/readyz")
    def readyz():
        with app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ready"}

    # Optional static UI (baked into container image)
    static_dir = Path(__file__).resolve().parents[1] / "static"
    if static_dir.is_dir():
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def spa_index():
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()
