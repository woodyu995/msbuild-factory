from __future__ import annotations

from typing import Any

import yaml


SUPPORTED_WINDOWS_RELEASES = {"ltsc2019", "ltsc2022"}


def render_windows_builder_pod(
    *,
    image_digest: str,
    windows_base: str,
    request_id: str,
    cpu_request: str = "2",
    cpu_limit: str = "8",
    memory_request: str = "4Gi",
    memory_limit: str = "16Gi",
    ephemeral_request: str = "20Gi",
    ephemeral_limit: str = "80Gi",
    pull_secret_name: str = "internal-registry-secret",
    purpose: str = "msbuild",
) -> dict[str, Any]:
    if windows_base not in SUPPORTED_WINDOWS_RELEASES:
        raise ValueError(f"unsupported windowsBase: {windows_base}")
    if not image_digest.startswith("sha256:"):
        # allow bare digest or full image@sha256
        if "@sha256:" in image_digest:
            image_ref = image_digest
        else:
            raise ValueError("image_digest must be sha256:... or image@sha256:...")
    else:
        image_ref = f"registry.internal/build/msbuild-profile@{image_digest}"

    safe_name = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in request_id).lower()
    pod_name = f"msbuild-{safe_name}"[:63].strip("-")

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": {
                "app.kubernetes.io/name": "msbuild-project-build",
                "build.company.io/request-id": request_id,
                "build.company.io/windows-release": windows_base,
                "build.company.io/purpose": purpose,
            },
        },
        "spec": {
            "os": {"name": "windows"},
            "restartPolicy": "Never",
            "nodeSelector": {
                "kubernetes.io/os": "windows",
                "build.company.io/windows-release": windows_base,
                "build.company.io/purpose": purpose,
            },
            "tolerations": [
                {
                    "key": "build.company.io/windows",
                    "operator": "Equal",
                    "value": "true",
                    "effect": "NoSchedule",
                }
            ],
            "imagePullSecrets": [{"name": pull_secret_name}],
            "containers": [
                {
                    "name": "builder",
                    "image": image_ref,
                    "imagePullPolicy": "IfNotPresent",
                    "workingDir": "C:\\workspace",
                    "resources": {
                        "requests": {
                            "cpu": cpu_request,
                            "memory": memory_request,
                            "ephemeral-storage": ephemeral_request,
                        },
                        "limits": {
                            "cpu": cpu_limit,
                            "memory": memory_limit,
                            "ephemeral-storage": ephemeral_limit,
                        },
                    },
                    "env": [
                        {"name": "BUILD_REQUEST_ID", "value": request_id},
                        {"name": "WINDOWS_RELEASE", "value": windows_base},
                    ],
                }
            ],
        },
    }


def render_windows_builder_pod_yaml(**kwargs: Any) -> str:
    pod = render_windows_builder_pod(**kwargs)
    return yaml.safe_dump(pod, sort_keys=False)


def jenkins_kubernetes_pod_template(
    *,
    image_digest: str,
    windows_base: str,
    request_id: str,
) -> str:
    """YAML fragment suitable for Jenkins Kubernetes plugin `yaml` field."""
    pod = render_windows_builder_pod(
        image_digest=image_digest,
        windows_base=windows_base,
        request_id=request_id,
    )
    # Jenkins plugin expects Pod spec-focused YAML; keep full Pod for clarity.
    return yaml.safe_dump(pod, sort_keys=False)
