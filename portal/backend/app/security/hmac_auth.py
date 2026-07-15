from __future__ import annotations

import hashlib
import hmac
import time


class CallbackAuthError(Exception):
    pass


def sign_body(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_hmac(
    *,
    secret: str,
    timestamp_header: str | None,
    signature_header: str | None,
    body: bytes,
    skew_seconds: int = 300,
    now: int | None = None,
) -> None:
    if not timestamp_header or not signature_header:
        raise CallbackAuthError("missing HMAC headers")
    try:
        ts = int(timestamp_header)
    except ValueError as exc:
        raise CallbackAuthError("invalid timestamp") from exc
    current = int(time.time() if now is None else now)
    if abs(current - ts) > skew_seconds:
        raise CallbackAuthError("timestamp outside allowed window")

    expected = sign_body(secret, timestamp_header, body)
    provided = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise CallbackAuthError("invalid signature")