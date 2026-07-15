from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    name: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def can_simulate(self) -> bool:
        return self.is_admin or "operator" in self.roles


def parse_api_tokens(raw: str | None) -> dict[str, Actor]:
    """Parse PORTAL_API_TOKENS=name:token:role1|role2,name2:token2:viewer"""
    mapping: dict[str, Actor] = {}
    if not raw:
        return mapping
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        bits = item.split(":")
        if len(bits) < 2:
            continue
        name, token = bits[0].strip(), bits[1].strip()
        roles = frozenset((bits[2] if len(bits) > 2 else "builder").replace("|", ",").split(","))
        roles = frozenset(r.strip() for r in roles if r.strip())
        if not name or not token:
            continue
        mapping[token] = Actor(name=name, roles=roles or frozenset({"builder"}))
    return mapping


def extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return None


def constant_time_token_lookup(tokens: dict[str, Actor], provided: str) -> Actor | None:
    # Avoid leaking which token matched via timing of dict get on long maps;
    # still O(n) compare.
    found: Actor | None = None
    for token, actor in tokens.items():
        if hmac.compare_digest(token, provided):
            found = actor
    return found


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
