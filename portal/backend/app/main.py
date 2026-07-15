from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.db.models import make_session_factory
from app.domain.catalog import load_catalog
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
        with session_factory() as session:
            seed_preset_images(session, catalog)
            session.commit()
        yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/healthz")
    def healthz():
        return {
            "status": "ok",
            "simulateWorkers": settings.simulate_workers,
            "jenkinsConfigured": bool(settings.jenkins_url and settings.jenkins_api_token),
            "insecureDefaults": settings.allow_insecure_defaults and settings.is_default_hmac_secret,
        }

    return app


app = create_app()