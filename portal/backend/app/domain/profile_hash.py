"""RFC 8785-inspired JSON Canonicalization for Build Input Profiles.

Implements the subset needed for our profiles: objects, arrays, strings,
booleans, null-omission, and integers. Floating point is rejected.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class CanonicalizationError(ValueError):
    pass


def _encode_string(value: str) -> str:
    # Use JSON encoder with ensure_ascii to get RFC-compatible escapes,
    # then strip surrounding quotes handling.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonicalize(value: Any) -> str:
    if value is None:
        raise CanonicalizationError("null leaves must be omitted by caller")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        raise CanonicalizationError("floating point values are not supported in profiles")
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, list):
        parts = [canonicalize(item) for item in value]
        return "[" + ",".join(parts) + "]"
    if isinstance(value, dict):
        items: list[str] = []
        for key in sorted(value.keys()):
            if not isinstance(key, str):
                raise CanonicalizationError("object keys must be strings")
            child = value[key]
            if child is None:
                continue
            items.append(f"{_encode_string(key)}:{canonicalize(child)}")
        return "{" + ",".join(items) + "}"
    raise CanonicalizationError(f"unsupported type: {type(value)!r}")


def profile_hash(normalized_profile: dict[str, Any]) -> str:
    canonical = canonicalize(normalized_profile)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_json(normalized_profile: dict[str, Any]) -> str:
    return canonicalize(normalized_profile)