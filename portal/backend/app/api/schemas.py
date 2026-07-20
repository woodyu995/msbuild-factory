from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EnvironmentSelection(BaseModel):
    visualStudio: str
    dotnetFrameworks: list[str] = Field(default_factory=list)
    dotnetSdks: list[str] = Field(default_factory=list)
    cppToolsets: list[str] = Field(default_factory=list)
    windowsSdks: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    reuseMode: Literal["preferCompatible", "exactReuse"] = "preferCompatible"


class ValidateRequest(BaseModel):
    environment: EnvironmentSelection


class ProjectInfo(BaseModel):
    repository: str
    gitRef: str
    solutionPath: str
    configuration: str = "Release"
    platform: str = "x64"


class NugetInfo(BaseModel):
    mode: Literal[
        "repo-packages",
        "internal-feed",
        "repo-packages-and-internal-feed",
    ] = "repo-packages-and-internal-feed"


class BuildRequestCreate(BaseModel):
    """Start a project build. Requires a READY image pin from /images/ensure."""

    project: ProjectInfo
    environment: EnvironmentSelection
    nuget: NugetInfo = Field(default_factory=NugetInfo)
    matchedProfileHash: str = Field(min_length=16)
    imageDigest: str = Field(min_length=8)


class EnsureImageRequest(BaseModel):
    environment: EnvironmentSelection


class ImageRef(BaseModel):
    repository: str
    tag: str
    digest: str


class ValidateResponse(BaseModel):
    valid: bool
    requestedProfileHash: str | None = None
    matchedProfileHash: str | None = None
    matchType: str | None = None
    imageStatus: str | None = None
    action: str
    estimatedWaitMinutes: int = 0
    providedCapabilities: list[str] = Field(default_factory=list)
    extraCapabilities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    image: ImageRef | None = None
    errorCode: str | None = None
    errorMessage: str | None = None


class EnsureImageResponse(BaseModel):
    requestedProfileHash: str
    matchedProfileHash: str | None = None
    matchType: str | None = None
    action: str
    imageStatus: str
    estimatedWaitMinutes: int = 0
    providedCapabilities: list[str] = Field(default_factory=list)
    extraCapabilities: list[str] = Field(default_factory=list)
    image: ImageRef | None = None
    windowsBase: str | None = None
    factoryLeaseId: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    ready: bool = False
    localImageRef: str | None = None
    localTarPath: str | None = None
    localTarFile: str | None = None


class ImageStatusResponse(BaseModel):
    profileHash: str
    imageStatus: str
    ready: bool
    image: ImageRef | None = None
    windowsBase: str | None = None
    factoryLeaseId: str | None = None
    leaseExpiresAt: str | None = None
    localImageRef: str | None = None
    localTarPath: str | None = None
    localTarFile: str | None = None


class BuildRequestResponse(BaseModel):
    id: str
    status: str
    repository: str
    gitRef: str
    resolvedCommit: str
    commitResolution: str | None = None
    solutionPath: str
    configuration: str
    platform: str
    requestedProfileHash: str
    matchedProfileHash: str | None
    matchType: str
    reuseMode: str
    imageDigest: str | None
    nugetMode: str
    jenkinsJobName: str | None
    jenkinsBuildNumber: int | None
    providedCapabilities: list[str] = Field(default_factory=list)
    extraCapabilities: list[str] = Field(default_factory=list)
    errorCode: str | None = None
    errorMessage: str | None = None
    image: ImageRef | None = None
    windowsBase: str | None = None
    environment: dict[str, Any] | None = None


class InternalBuildEvent(BaseModel):
    requestId: str
    eventType: str
    message: str
    jenkinsBuildNumber: int | None = None
    leaseId: str | None = None
    metadata: dict[str, Any] | None = None


class InternalImageStatus(BaseModel):
    status: str
    leaseId: str
    message: str | None = None
    imageDigest: str | None = None
    capabilityProfile: dict[str, Any] | None = None
    requestId: str | None = None
