from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class RegistryRepos:
    host: str
    final_repository: str
    staging_repository: str
    pull_secret_name: str

    @property
    def final_image(self) -> str:
        return f"{self.host}/{self.final_repository}"

    @property
    def staging_image(self) -> str:
        return f"{self.host}/{self.staging_repository}"


def registry_from_settings(settings: Settings) -> RegistryRepos:
    return RegistryRepos(
        host=settings.registry_host.rstrip("/"),
        final_repository=settings.registry_final_repo.strip("/"),
        staging_repository=settings.registry_staging_repo.strip("/"),
        pull_secret_name=settings.registry_pull_secret,
    )
