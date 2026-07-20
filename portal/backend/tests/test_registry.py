from app.config import Settings
from app.domain.dockerfile_gen import generate_install_manifest
from app.domain.registry import registry_from_settings


def test_registry_paths_from_settings():
    settings = Settings(
        registry_host="nexus.company.io:8082",
        registry_final_repo="build/msbuild-profile",
        registry_staging_repo="build/msbuild-profile-staging",
    )
    reg = registry_from_settings(settings)
    assert reg.final_image == "nexus.company.io:8082/build/msbuild-profile"
    assert reg.push_host == "nexus.company.io:8082"
    assert reg.push_final_image == reg.final_image
    assert reg.staging_image.endswith("/build/msbuild-profile-staging")


def test_registry_push_host_override():
    settings = Settings(
        registry_host="nexus.company.io:8082",
        registry_push_host="nexus-push.company.io:8082",
        registry_final_repo="build/msbuild-profile",
        registry_staging_repo="build/msbuild-profile-staging",
    )
    reg = registry_from_settings(settings)
    assert reg.host == "nexus.company.io:8082"
    assert reg.push_host == "nexus-push.company.io:8082"
    assert reg.final_image.startswith("nexus.company.io:8082/")
    assert reg.push_final_image.startswith("nexus-push.company.io:8082/")


def test_install_manifest_emits_logical_frameworks():
    manifest = generate_install_manifest(
        {
            "windowsBase": {"name": "ltsc2022", "digest": "sha256:x"},
            "agentBase": {"image": "agent", "digest": "sha256:y"},
            "visualStudio": {"generation": "2022", "components": []},
            "dotnetFrameworkTargetingPacks": [
                {"version": "4.8.1", "installerSha256": "sha256:z"},
            ],
            "dotnetSdks": [],
            "windowsSdks": [],
            "cppToolsets": [],
            "features": ["managed-desktop"],
            "customSdks": [],
        }
    )
    assert manifest["dotnetFrameworks"] == ["4.8"]
