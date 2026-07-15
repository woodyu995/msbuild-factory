import json
from pathlib import Path

from app.domain.capability_matcher import MatchCandidate, is_compatible, match_images
from app.domain.catalog import load_catalog

FIXTURES = Path(__file__).parent / "fixtures" / "capability-match"
CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "catalog.yaml"


def test_capability_fixtures():
    catalog = load_catalog(CATALOG)
    policy = catalog.capability_matching
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        candidates = [
            MatchCandidate(
                profile_hash=c["profileHash"],
                image_digest=c["imageDigest"],
                repository=c.get("repository", "registry.internal/build/msbuild-profile"),
                tag=c.get("tag", "t"),
                capability=c["capability"],
                windows_base=c["capability"]["windowsBase"],
                vs_generation=c["capability"]["visualStudio"],
                status=c.get("status", "READY"),
                hot=c.get("hot", False),
                size_gib=c.get("sizeGib", 50),
            )
            for c in data["candidates"]
        ]
        result = match_images(
            requested_hash=data["requestedHash"],
            capability_request=data["requestedCapability"],
            candidates=candidates,
            matching_policy=policy,
            reuse_mode=data.get("reuseMode", "preferCompatible"),
        )
        if data["expect"] is None:
            assert result is None, path.name
        else:
            assert result is not None, path.name
            assert result.match_type == data["expect"]["matchType"], path.name
            assert result.candidate.profile_hash == data["expect"]["matchedHash"], path.name


def test_newer_cpp_does_not_satisfy_older():
    policy = {"visualStudioGeneration": "exact", "approvedSdkRollForward": []}
    requested = {
        "visualStudio": "2022",
        "dotnetFrameworks": [],
        "dotnetSdks": [],
        "cppToolsets": ["v142"],
        "windowsSdks": [],
        "features": [],
        "windowsBase": "ltsc2022",
        "customSdks": [],
    }
    image = {
        "visualStudio": "2022",
        "dotnetFrameworks": [],
        "dotnetSdks": [],
        "cppToolsets": ["v143"],
        "windowsSdks": [],
        "features": [],
        "windowsBase": "ltsc2022",
        "customSdks": [],
    }
    assert not is_compatible(requested, image, matching_policy=policy)