from app.config import Settings
from app.domain.registry import registry_from_settings


def test_registry_paths_from_settings():
    settings = Settings(
        registry_host="nexus.nexus.svc.cluster.local:8082",
        registry_final_repo="build/msbuild-profile",
        registry_staging_repo="build/msbuild-profile-staging",
    )
    reg = registry_from_settings(settings)
    assert reg.final_image == "nexus.nexus.svc.cluster.local:8082/build/msbuild-profile"
    assert reg.staging_image.endswith("/build/msbuild-profile-staging")
