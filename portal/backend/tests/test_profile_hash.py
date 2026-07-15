import json
from pathlib import Path

import pytest

from app.domain.catalog import load_catalog
from app.domain.profile_hash import canonicalize, profile_hash
from app.domain.profile_resolver import resolve_profile

FIXTURES = Path(__file__).parent / "fixtures" / "profile-hash"
CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "catalog.yaml"


def test_canonical_key_order_and_hash_stability():
    a = {"b": 1, "a": [2, 1], "c": {"z": "x", "y": "w"}}
    b = {"c": {"y": "w", "z": "x"}, "a": [2, 1], "b": 1}
    assert canonicalize(a) == canonicalize(b)
    assert profile_hash(a) == profile_hash(b)


def test_profile_hash_fixtures(tmp_path=None):
    catalog = load_catalog(CATALOG)
    for fixture_path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        resolved = resolve_profile(catalog, data["environment"])
        assert resolved.profile_hash == data["expectedProfileHash"], fixture_path.name
        assert resolved.canonical_json == data["expectedCanonical"]


def test_array_order_does_not_change_hash():
    catalog = load_catalog(CATALOG)
    env1 = {
        "visualStudio": "2022",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": ["8.0"],
        "cppToolsets": [],
        "windowsSdks": [],
        "features": ["managed-desktop"],
    }
    env2 = {
        "visualStudio": "2022",
        "dotnetFrameworks": ["4.8"],
        "dotnetSdks": ["8.0"],
        "cppToolsets": [],
        "windowsSdks": [],
        "features": ["managed-desktop"],
    }
    # features shuffled before resolve — resolver sorts
    env2["features"] = ["managed-desktop"]
    assert resolve_profile(catalog, env1).profile_hash == resolve_profile(catalog, env2).profile_hash