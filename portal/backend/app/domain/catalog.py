from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Catalog:
    raw: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.raw["catalogVersion"])

    @property
    def mvp_factory_enabled(self) -> bool:
        return bool(self.raw.get("mvpFactoryEnabled", False))

    @property
    def compatible_workers(self) -> list[str]:
        return list(self.raw.get("mvpCompatibleWorkers", []))

    @property
    def presets(self) -> list[dict[str, Any]]:
        return list(self.raw.get("presets", []))

    @property
    def visual_studio(self) -> dict[str, Any]:
        return dict(self.raw["visualStudio"])

    @property
    def installers(self) -> dict[str, Any]:
        return dict(self.raw.get("installers", {}))

    @property
    def compatibility_rules(self) -> list[dict[str, Any]]:
        return list(self.raw.get("compatibilityRules", []))

    @property
    def capability_matching(self) -> dict[str, Any]:
        return dict(self.raw.get("capabilityMatching", {}))

    @property
    def estimated_minutes(self) -> dict[str, Any]:
        return dict(self.raw.get("estimatedImageBuildMinutes", {}))

    def vs_entry(self, generation: str) -> dict[str, Any]:
        try:
            return self.visual_studio[generation]
        except KeyError as exc:
            raise ValueError(f"Unsupported Visual Studio generation: {generation}") from exc


def load_catalog(path: Path) -> Catalog:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("Catalog must be a mapping")
    return Catalog(raw=data)