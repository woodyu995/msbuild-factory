from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import httpx


@dataclass
class TriggerResult:
    queue_id: str
    job_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    mode: str = "recording"


class JenkinsClient(Protocol):
    def trigger_job(self, job_name: str, parameters: dict[str, Any]) -> TriggerResult: ...


class RecordingJenkinsClient:
    """In-process stub used for local/dev and tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._counter = 0

    def trigger_job(self, job_name: str, parameters: dict[str, Any]) -> TriggerResult:
        self._counter += 1
        self.calls.append((job_name, dict(parameters)))
        return TriggerResult(
            queue_id=f"q-{self._counter}",
            job_name=job_name,
            parameters=dict(parameters),
            mode="recording",
        )


class HttpJenkinsClient:
    """Minimal Jenkins buildWithParameters client (user + API token)."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        api_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def trigger_job(self, job_name: str, parameters: dict[str, Any]) -> TriggerResult:
        self.calls.append((job_name, dict(parameters)))
        url = f"{self.base_url}/job/{quote(job_name, safe='')}/buildWithParameters"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                url,
                params={k: str(v) for k, v in parameters.items()},
                auth=(self.username, self.api_token),
            )
            response.raise_for_status()
            queue_id = response.headers.get("Location") or response.headers.get("location") or "unknown"
        return TriggerResult(
            queue_id=queue_id,
            job_name=job_name,
            parameters=dict(parameters),
            mode="http",
        )


_default_client: JenkinsClient = RecordingJenkinsClient()


def configure_jenkins_client(
    *,
    base_url: str | None = None,
    username: str | None = None,
    api_token: str | None = None,
) -> JenkinsClient:
    global _default_client
    if base_url and username and api_token:
        _default_client = HttpJenkinsClient(
            base_url=base_url,
            username=username,
            api_token=api_token,
        )
    else:
        _default_client = RecordingJenkinsClient()
    return _default_client


def get_jenkins_client() -> JenkinsClient:
    return _default_client


def reset_jenkins_client() -> RecordingJenkinsClient:
    global _default_client
    client = RecordingJenkinsClient()
    _default_client = client
    return client
