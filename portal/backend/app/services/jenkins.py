from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class TriggerResult:
    queue_id: str
    job_name: str
    parameters: dict[str, Any] = field(default_factory=dict)


class JenkinsClient(Protocol):
    def trigger_job(self, job_name: str, parameters: dict[str, Any]) -> TriggerResult: ...


class RecordingJenkinsClient:
    """In-process stub used until a real Jenkins endpoint is wired."""

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
        )


_default_client = RecordingJenkinsClient()


def get_jenkins_client() -> RecordingJenkinsClient:
    return _default_client


def reset_jenkins_client() -> RecordingJenkinsClient:
    global _default_client
    _default_client = RecordingJenkinsClient()
    return _default_client