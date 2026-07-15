from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = BACKEND_ROOT / "catalog" / "catalog.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTAL_", env_file=".env", extra="ignore")

    app_name: str = "MSBuild Build Portal"
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'portal.db'}"
    catalog_path: Path = DEFAULT_CATALOG
    callback_hmac_secret: str = "dev-callback-secret-change-me"
    callback_timestamp_skew_seconds: int = 300
    # API auth: set PORTAL_REQUIRE_AUTH=true and PORTAL_API_TOKENS=user:token:role
    require_auth: bool = False
    api_tokens: str | None = None
    default_actor: str = "local-dev"
    # Default is builder-only; grant operator/admin via PORTAL_API_TOKENS or override for local sim.
    default_actor_roles: str = "builder"
    # None = follow catalog.mvpFactoryEnabled; True/False overrides
    factory_enabled: bool | None = None
    factory_lease_minutes: int = 135
    # Local/dev: auto-simulate factory + project build after queueing
    simulate_workers: bool = False
    # When false, refuse to boot with the default HMAC secret.
    allow_insecure_defaults: bool = True
    jenkins_url: str | None = None
    jenkins_username: str | None = None
    jenkins_api_token: str | None = None
    # Git resolve: placeholder | ls_remote | http_api
    git_resolve_mode: str = "placeholder"
    git_url_template: str | None = None  # https://git.internal/{repository}.git
    git_http_base_url: str | None = None
    git_token: str | None = None
    git_require_exact: bool = False
    # Nexus (or other) Docker registry — factory pushes images here
    # e.g. nexus.nexus.svc.cluster.local:8082 or nexus.company.io
    registry_host: str = "nexus.company.io"
    registry_final_repo: str = "build/msbuild-profile"
    registry_staging_repo: str = "build/msbuild-profile-staging"
    registry_pull_secret: str = "nexus-docker-pull"
    # Comma-separated CORS origins; empty = allow all (dev only)
    cors_origins: str = "*"

    @property
    def is_default_hmac_secret(self) -> bool:
        return self.callback_hmac_secret == "dev-callback-secret-change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()