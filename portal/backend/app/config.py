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
    # MVP auth bypass for local UI; set PORTAL_REQUIRE_AUTH=true in prod-like envs
    require_auth: bool = False
    default_actor: str = "local-dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()