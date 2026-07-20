"""Shared helpers for portal API tests."""

from __future__ import annotations

HOT_ENV = {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": ["8.0"],
    "cppToolsets": [],
    "windowsSdks": [],
    "features": ["managed-desktop"],
    "reuseMode": "preferCompatible",
}


def ensure_ready(client, environment: dict | None = None) -> dict:
    """POST /images/ensure and require a READY pin."""
    env = environment or HOT_ENV
    resp = client.post("/api/v1/images/ensure", json={"environment": env})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True, body
    assert body["matchedProfileHash"]
    assert body["image"]["digest"]
    return body


def build_payload(
    *,
    ensured: dict,
    project: dict | None = None,
    environment: dict | None = None,
) -> dict:
    return {
        "project": project
        or {
            "repository": "ProductClient",
            "gitRef": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "solutionPath": "ProductClient.sln",
        },
        "environment": environment or HOT_ENV,
        "matchedProfileHash": ensured["matchedProfileHash"],
        "imageDigest": ensured["image"]["digest"],
    }
