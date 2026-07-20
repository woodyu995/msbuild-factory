from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class RegistryRepos:
    host: str
    push_host: str
    final_repository: str
    staging_repository: str
    pull_secret_name: str

    @property
    def final_image(self) -> str:
        """Pull/reference path stored on BuildImage and used in pod templates."""
        return f"{self.host}/{self.final_repository}"

    @property
    def staging_image(self) -> str:
        return f"{self.host}/{self.staging_repository}"

    @property
    def push_final_image(self) -> str:
        """Tag/push destination for Image Factory (may differ DNS from pull host)."""
        return f"{self.push_host}/{self.final_repository}"

    @property
    def push_staging_image(self) -> str:
        return f"{self.push_host}/{self.staging_repository}"


def registry_from_settings(settings: Settings) -> RegistryRepos:
    host = settings.registry_host.rstrip("/")
    push = (settings.registry_push_host or settings.registry_host).rstrip("/")
    return RegistryRepos(
        host=host,
        push_host=push,
        final_repository=settings.registry_final_repo.strip("/"),
        staging_repository=settings.registry_staging_repo.strip("/"),
        pull_secret_name=settings.registry_pull_secret,
    )
