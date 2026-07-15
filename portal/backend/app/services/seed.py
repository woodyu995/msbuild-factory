from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BuildImage, BuildImageCapability, utcnow
from app.domain.capability_matcher import capability_keys
from app.domain.catalog import Catalog
from app.domain.profile_resolver import resolve_profile
from app.domain.registry import registry_from_settings
from app.services.image_resolve import capability_from_environment, ensure_profile_row


def _default_image_repository() -> str:
    from app.config import get_settings

    return registry_from_settings(get_settings()).final_image


def seed_preset_images(session: Session, catalog: Catalog, actor: str = "seed") -> list[str]:
    """Create READY Hot preset images for MVP (factory closed)."""
    created: list[str] = []
    for preset in catalog.presets:
        env = dict(preset["environment"])
        env.setdefault("reuseMode", "preferCompatible")
        resolved = resolve_profile(catalog, env)
        ensure_profile_row(session, catalog, resolved, actor)

        existing = session.scalar(
            select(BuildImage).where(
                BuildImage.profile_hash == resolved.profile_hash,
                BuildImage.status != "DELETED",
            )
        )
        if existing and existing.status == "READY":
            continue

        capability = capability_from_environment(resolved)
        # Hot presets intentionally expose the requested set; managed-dotnet8 also has net48
        tag = f"{preset['id']}-{resolved.profile_hash[:12]}"
        digest = f"sha256:preset-{preset['id']}-{resolved.profile_hash[:16]}"
        image = existing or BuildImage(
            profile_hash=resolved.profile_hash,
            image_repository=_default_image_repository(),
            image_tag=tag,
            image_digest=digest,
            status="READY",
            base_image_digest=resolved.build_input["windowsBase"]["digest"],
            windows_base=resolved.windows_base,
            vs_generation=resolved.vs_generation,
            capability_profile_json=json.dumps(capability, ensure_ascii=False),
            catalog_version=catalog.version,
            hot=bool(preset.get("hot")),
            size_gib=45.0 if preset["id"].startswith("managed") else 70.0,
            ready_at=utcnow(),
            last_used_at=utcnow(),
        )
        if existing:
            image.status = "READY"
            image.image_tag = tag
            image.image_digest = digest
            image.capability_profile_json = json.dumps(capability, ensure_ascii=False)
            image.hot = bool(preset.get("hot"))
            image.ready_at = utcnow()
        else:
            session.add(image)
            session.flush()

        # refresh capability rows
        for cap in list(image.capabilities):
            session.delete(cap)
        session.flush()
        for key in sorted(capability_keys(capability)):
            version = ""
            if key.startswith("dotnet-sdk-") and key.count(".") >= 2:
                version = key.removeprefix("dotnet-sdk-")
            session.add(
                BuildImageCapability(
                    build_image_id=image.id,
                    capability_key=key,
                    capability_version=version,
                )
            )
        created.append(resolved.profile_hash)
    session.flush()
    return created