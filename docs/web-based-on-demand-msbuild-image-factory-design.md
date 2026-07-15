# 추천 구현안: Web 기반 주문형 MSBuild 이미지 팩토리 (개정 2)

> 본 문서는 초안·1차 개정안을 검토한 뒤, **Capability 기반 이미지 재사용(포함 관계 매칭)**, Profile Hash 역할 재정의, 동시성 Lock, 비동기 오케스트레이션, Windows 컨테이너 운영 제약, NuGet/폐쇄망, 보안 MVP를 반영한 개정 2안이다.

## 1. 개요

핵심 구조는 다음과 같다.

> 사용자가 Web에서 필요한 빌드 도구를 선택하고, 시스템이 Requested Profile을 정규화한다. 내부 Registry에 **요청을 충족하는** 이미지가 있으면 재사용하며(완전 일치 또는 Capability 상위집합), 없으면 전용 Image Factory가 요청 Profile 기준으로 최초 한 번 생성·검증·저장한 후 Kubernetes Windows Pod에서 프로젝트를 빌드한다.

프로젝트 빌드 Pod가 시작될 때마다 Visual Studio Build Tools를 설치하는 방식은 사용하지 않는다.

### 1.1 핵심 구현 원칙

> 사용자가 이미지를 선택하는 것이 아니라 필요한 빌드 도구를 선택하고, 시스템이 재현 가능한 Profile로 변환한다. **이미지 재사용은 Profile Hash 완전 일치가 아니라, 기존 이미지가 요청 Capability를 모두 제공하는지(⊆)로 판단한다.** Hash는 요청 식별·중복 생성 Lock·감사·재현 추적에 쓴다.

### 1.2 개정 2에서 강화·변경한 계약

| 영역 | 강화·변경 내용 |
|------|----------------|
| 이미지 재사용 | Exact Hash 우선 + **Capability Superset 매칭** + 점수 기반 선택 |
| Profile 모델 | Requested Profile / Image Capability Profile / Build Input Profile 분리 |
| Profile Hash | Canonical JSON(RFC 8785) + fixture. **재사용 유일 기준이 아님** |
| 설치 원본 핀 | Targeting Pack·SDK·Windows SDK·Layout 매니페스트까지 SHA/digest 핀 |
| 동시성 | `CREATING` + DB 락 + lease CAS Callback + waiter/실패 reconcile |
| 오케스트레이션 | 이미지 생성과 프로젝트 빌드 비동기 분리 |
| 검증·Push | 스테이징 검증 성공 후에만 final 태그/digest 게시 → READY |
| Windows 운영 | OS 호환, 디스크, Pull SLA, Offline Layout RO 마운트 |
| 조합 폭발 | Allow-list + 쿼터 + Hot/Cold + GC + Superset 부작용 정책 |
| 보안 | MVP부터 Internal Callback 인증 필수 |
| NuGet | 폐쇄망 restore 모델 명시 |

---

## 2. 전체 아키텍처

```text
┌───────────────────────────────┐
│ 사용자 Web Browser            │
│ 프로젝트 및 빌드 환경 선택    │
└───────────────┬───────────────┘
                │ HTTPS + SSO
                ▼
┌───────────────────────────────┐
│ Build Portal (Source of Truth)│
│                               │
│ React Web UI                  │
│ Build Portal API              │
│ PostgreSQL                    │
│ Profile Resolver              │
│ Capability Matcher            │
│ Image Lifecycle Controller    │
│ Reconciliation Worker         │
└───────┬───────────────┬───────┘
        │               │
        │ 신규 생성 필요 │ 충족 이미지 READY
        ▼               ▼
┌──────────────────┐  ┌────────────────────────────┐
│ Image Factory    │  │ Jenkins Project Build Job  │
│ (전용 Windows)   │  │                            │
│                  │  │ Windows K8s Agent Pod      │
│ Dockerfile 생성  │  │ Checkout / NuGet / MSBuild │
│ Build Tools 설치 │  │ Test / Artifact Publish    │
│ 검증 / Push      │  └─────────────┬──────────────┘
└────────┬─────────┘                │
         │ Callback(HMAC/mTLS)      │ Callback
         ▼                          ▼
┌─────────────────────────────────────────────────┐
│ Portal: build_image / build_request 상태 갱신   │
└─────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│ 내부 Registry / Artifact Repository             │
└─────────────────────────────────────────────────┘
```

### 2.1 책임 경계

| 구성요소 | Source of Truth | 역할 |
|----------|-----------------|------|
| Build Portal | Profile, Capability, Image 상태, Build Request | 검증, Hash, Matcher, Lock, 상태 전이, UI |
| Image Factory | 없음(실행기) | 이미지 빌드·검증·Push·**실측 Capability 보고** 후 Callback |
| Jenkins | 빌드 실행 로그/아티팩트 | **READY digest로 프로젝트 빌드만** |
| Registry | 이미지 blob/digest | 보관, GC 대상 |

Jenkins는 Orchestrator 겸용으로 시작하되, **장시간 Image Factory를 `wait: true`로 붙잡지 않는다.** Portal이 상태 머신의 주인이다.

---

## 3. 구성요소별 역할

### 3.1 Build Portal Web

```text
Frontend: TypeScript, React, Vite, MUI
Backend:  FastAPI, PostgreSQL
배포:     Kubernetes Deployment
```

제공 기능:

- 프로젝트 저장소와 Git ref 선택 (`resolved_commit`은 접수 시 고정)
- 솔루션 경로, Configuration, Platform 선택
- 승인된 빌드 도구 / Preset 선택 (VS 세대별 제약 반영)
- Exact / Compatible Superset 매칭 결과·예상 대기 안내
- 신규 이미지 생성 진행 상태(SSE, `Last-Event-ID` 재개)
- Jenkins 빌드 상태·로그·결과물
- 과거 빌드 환경 재사용(DEPRECATED 포함 정책, QUARANTINED 제외)
- 재현 빌드 모드(`exactReuse: true`) 시 Exact만 허용

사용자는 Docker 이미지 이름, Windows 베이스, Dockerfile을 직접 입력하지 않는다.

### 3.2 Jenkins

```text
READY 이미지로 Windows Agent Pod 생성
MSBuild Pipeline 실행
Artifact 수집
Portal Callback으로 상태 전달
```

이미지 미존재 시 Jenkins가 Factory를 동기 대기하지 않는다. Portal이 Factory를 큐잉하고, READY 후 Project Build Job만 트리거한다.

### 3.3 Windows Image Factory

**전용 Windows Server VM 또는 전용 Windows Builder 노드.**  
일반 Kubernetes Windows Pod 안에서 Docker-in-Docker로 이미지 빌드하는 방식은 MVP에서 채택하지 않는다.

```text
Image Factory
├─ Docker Engine (Windows containers)
├─ 내부 Registry Push 권한 (Project Build와 계정 분리)
├─ Visual Studio Offline Layout (RO 마운트/캐시)
├─ .NET/Windows SDK 등 독립 설치 원본
├─ Dockerfile 템플릿 / 설치·검증 스크립트
└─ 검증용 샘플 프로젝트
```

일반 프로젝트 빌드 Pod에는 이미지 생성·Push 권한을 부여하지 않는다.

### 3.4 내부 Registry

```text
registry.internal/build/
├─ agent-base-ltsc2019
├─ agent-base-ltsc2022
├─ msbuild-profile          # final (READY만)
└─ msbuild-profile-staging  # 검증 전용 (실패 시 GC)
   ├─ vs2019-net46-<hash12>
   ├─ vs2022-net48-dotnet8-<hash12>
   └─ vs2022-v143-mfc-<hash12>
```

프로젝트 빌드는 **태그보다 digest**를 사용한다.

---

## 4. Web 빌드 환경 선택

### 4.1 프로젝트 정보

```text
Repository / Git Ref / Solution Path / Configuration / Platform
```

접수 시 Portal이 `resolved_commit`을 확정·저장하고, 이후 빌드는 해당 SHA만 checkout한다.

### 4.2 빌드 환경 (사용자 선택)

| 항목 | 선택 | 비고 |
|------|------|------|
| Visual Studio Build Tools | 2019 / 2022 | 단수 |
| .NET Framework Targeting Pack | 복수 | Allow-list |
| .NET SDK | 복수 (논리 버전 6.0, 8.0) | 저장 시 정확한 패치·SHA로 해석 |
| C++ Toolset | 없음 / v142 / v143 | VS 세대 제약 |
| Windows SDK | 복수 | 정확한 빌드 번호 |
| 추가 기능 | Managed Desktop, MFC, ATL, C++/CLI, WiX, 사내 SDK | Allow-list |
| reuseMode | `preferCompatible` (기본) / `exactReuse` | 재현 빌드용 |

### 4.3 시스템이 자동 결정

```text
Windows Server Core 버전
베이스·Agent 이미지 digest
Visual Studio Component ID
설치 원본 버전 + installerSha256 / layoutManifestDigest
Jenkins Agent / JRE 버전
Dockerfile 템플릿
Kubernetes Node Selector / OS 격리 요구
```

예: `VS2019 + .NET Framework 4.6` → `ltsc2019` + `build.company.io/windows-release=ltsc2019`.

### 4.4 Preset과 고급 선택

초기 화면은 검증된 Preset만 노출한다. Preset 이미지는 의도적으로 **넓은 Capability 번들**(Hot)일 수 있으며, Capability Matcher의 주 재사용 대상이다.

```text
표준 .NET Framework 4.6
표준 .NET Framework 4.8
표준 .NET 8 Windows
Visual C++ v142
Visual C++ v143
Visual C++ v143 + MFC/ATL
사용자 정의 (관리자/권한 사용자)
```

완전히 자유로운 조합이 아니라 **서버 Allow-list + 호환 그래프**에 포함된 조합만 허용한다.

---

## 5. Component Catalog

폐쇄망 설치 가능 구성요소를 카탈로그로 관리한다. Web 선택지는 **Offline Layout 또는 반입된 독립 설치 원본에 실제 포함된 항목**으로 제한한다.

```yaml
catalogVersion: "2026.07"

visualStudio:
  "2019":
    windowsBase: ltsc2019
    layoutRelease: vs2019-16.11.54
    layoutManifestDigest: "sha256:layout-2019-..."
    allowedComponents:
      - msbuild
      - managed-desktop
      - net46-targeting-pack
      - net462-targeting-pack
      - net472-targeting-pack
      - net48-targeting-pack
      - cpp-v142
      - windows-sdk-19041

  "2022":
    windowsBase: ltsc2022
    layoutRelease: vs2022-17.14.x
    layoutManifestDigest: "sha256:layout-2022-..."
    allowedComponents:
      - msbuild
      - managed-desktop
      - net48-targeting-pack
      - dotnet-sdk-8
      - cpp-v142
      - cpp-v143
      - mfc
      - atl
      - cpp-cli
      - windows-sdk-19041
      - windows-sdk-22621

# 호환·매칭 규칙은 Catalog/API/검증기가 동일 DSL을 사용한다.
compatibility:
  rules:
    - when: { visualStudio: "2019" }
      deny: [cpp-v143, dotnet-sdk-8]
    - when: { featuresIncludes: ["mfc"] }
      requireAny: { cppToolsets: ["v142", "v143"] }

capabilityMatching:
  visualStudioGeneration: exact          # 기본: 동일 세대만
  allowNewerVsForOlderProjects: false    # 정책 플래그로만 개방
  dotnetSdk: exactPatchOrApprovedRollForward
  cppToolset: exact                      # 상위 toolset ≠ 충족
  windowsSdk: exact                      # 상위 SDK ≠ 충족 (정책 예외 가능)
  features: subsetRequired
  customSdks: exactVersion

installers:
  "dotnet-sdk-8.0":
    resolvedVersion: "8.0.412"
    installerSha256: "ab..."
  "net48-targeting-pack":
    resolvedVersion: "4.8.1.x"
    installerSha256: "cd..."
  "windows-sdk-22621":
    resolvedVersion: "10.0.22621.0"
    installerSha256: "ef..."
```

논리 키 → VS Component ID / 독립 installer 매핑은 카탈로그에 두고 버전 관리한다.

### 5.1 Offline Layout만으로 설치 가능한가?

**아니다.** Layout으로 MSBuild·다수 Workload/Component는 설치 가능하나, 다음을 **독립 반입**해야 한다.

| 항목 | 사유 |
|------|------|
| .NET SDK Windows Installer | Layout 외 채널 |
| 일부 .NET Framework Targeting/Developer Pack | Layout 미포함·별도 패키지인 경우 |
| 특정 Windows SDK 빌드 | Layout에 없거나 버전이 다른 경우 |
| WiX, SSDT, 사내 SDK, 커스텀 targets | 조직 전용 |
| JRE / Jenkins Agent / 사내 CA | agent-base 구성 |

Catalog `allowed*`는 반입·체크섬 검증이 끝난 항목만 켠다.

---

## 6. Profile 모델 (3종 분리)

### 6.1 Requested Build Profile (사용자 요청)

배열 필드는 항상 배열로 받는다. (`windowsSdk` 단수 금지)

```json
{
  "visualStudio": "2022",
  "dotnetFrameworks": ["4.8"],
  "dotnetSdks": ["8.0"],
  "cppToolsets": [],
  "windowsSdks": [],
  "features": ["managed-desktop"],
  "reuseMode": "preferCompatible"
}
```

### 6.2 Image Capability Profile (이미지가 실제로 제공)

검증 성공 후 Factory가 보고하고 Portal이 `build_image`에 저장한다.

```json
{
  "visualStudio": "2022",
  "dotnetFrameworks": ["4.6.2", "4.7.2", "4.8"],
  "dotnetSdks": [
    { "version": "8.0.412", "rollForwardPolicy": "latestPatch" }
  ],
  "cppToolsets": ["v142", "v143"],
  "windowsSdks": ["10.0.19041.0", "10.0.22621.0"],
  "features": ["managed-desktop", "mfc", "atl"],
  "windowsBase": "ltsc2022",
  "customSdks": []
}
```

요청에 없는 C++/MFC가 있어도, 요청 Capability를 모두 제공하면 **재사용 후보**가 된다.

### 6.3 Build Input Profile (이미지 생성 입력, Hash 대상)

신규 이미지 생성·Exact 재현에 쓰는 완전 핀된 Profile이다.

Portal Profile Resolver가 수행:

1. Allow-list·호환 그래프 검증 (실패 시 `PROFILE_REJECTED`)
2. 배열 필드 **사전식 정렬·중복 제거**
3. 논리 버전 → 설치 원본 버전 + `installerSha256` 해석
4. VS 세대 → `windowsBase`, `layoutRelease`, `layoutManifestDigest`, Component ID 목록
5. 현재 게시된 `agent-base` digest, base image digest, template/validation/script 버전 삽입
6. `schemaVersion` 부여

```json
{
  "schemaVersion": 2,
  "windowsBase": {
    "name": "ltsc2022",
    "digest": "sha256:base-image-digest"
  },
  "agentBase": {
    "image": "registry.internal/build/agent-base-ltsc2022",
    "digest": "sha256:agent-base-digest"
  },
  "visualStudio": {
    "generation": "2022",
    "layoutRelease": "vs2022-17.14.x",
    "layoutManifestDigest": "sha256:layout-2022-...",
    "components": [
      "Microsoft.Component.MSBuild",
      "Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools"
    ]
  },
  "dotnetFrameworkTargetingPacks": [
    {
      "version": "4.8.1.x",
      "installerSha256": "cd..."
    }
  ],
  "dotnetSdks": [
    {
      "version": "8.0.412",
      "installerSha256": "ab..."
    }
  ],
  "cppToolsets": [],
  "windowsSdks": [],
  "features": ["managed-desktop"],
  "customSdks": [],
  "templateVersion": "image-template-3",
  "installScriptVersion": "install-scripts-7",
  "validationSuiteVersion": "validation-5"
}
```

---

## 7. Profile Hash (Canonical) — 역할 재정의

### 7.1 정의

```text
canonicalBytes = JCS(buildInputProfile)   # RFC 8785
profileHash    = hex(SHA-256(canonicalBytes))
```

Hash 입력에 반드시 포함:

```text
사용자 선택 구성요소 (정규화·핀 후)
Windows 베이스 이미지 digest
공통 Agent 이미지 digest
Visual Studio Offline Layout 릴리스 + layoutManifestDigest
.NET SDK / Targeting Pack / Windows SDK 등 installerSha256
Dockerfile 템플릿·설치·검증 스크립트 버전
schemaVersion
```

### 7.2 Canonicalization 계약

- UTF-8, 키 이름 사전식 정렬, 불필요 공백 없음
- 의미상 집합인 배열은 Resolver가 정렬 후 직렬화
- `null` 필드 생략, 빈 배열은 유지
- 숫자·문자열 이스케이프는 RFC 8785 준수
- fixture: `tests/fixtures/profile-hash/*.json` → 기대 `profileHash`

동일 사용자 선택이어도 base/레이아웃/설치 원본이 바뀌면 새 Hash가 된다. 의도된 동작이다.

### 7.3 Hash로 하는 일 / 하지 않는 일

| 한다 | 하지 않는다 |
|------|-------------|
| 동일 요청 식별 | **이미지 재사용의 유일 기준** |
| Exact READY 조회 1순위 | Capability 충족 여부 판단 |
| 신규 생성 Lock 키 (`CREATING`) | Superset 후보 배제 |
| 감사·재현·생성 입력 추적 | |

`reuseMode=exactReuse`일 때만 Hash 일치 이미지만 사용한다.

### 7.4 이미지 참조

```text
tag:    registry.internal/build/msbuild-profile:vs2022-net48-dotnet8-83c87a10f35b
digest: registry.internal/build/msbuild-profile@sha256:...
```

빌드 Pod는 digest만 사용한다. 태그 접두 + hash12는 운영 편의용이다.

---

## 8. Capability 매칭과 이미지 선택

### 8.1 기본 원칙

```text
사용자가 요청한 Capability 집합  ⊆  기존 이미지가 제공하는 Capability 집합
```

단순 “버전이 더 많음 = 호환”이 아니다. 아래 특수 규칙을 적용한다.

### 8.2 단순 집합 비교로 처리하면 안 되는 항목

| 항목 | 규칙 |
|------|------|
| VS / MSBuild 세대 | 기본 **exact**. 상위 세대로 구형 프로젝트 허용은 `allowNewerVsForOlderProjects` 플래그 |
| Windows Base ↔ Worker | 이미지 `windowsBase`와 실행 노드 라벨이 호환되어야 함. 불호환이면 후보 제외 |
| .NET SDK | 요청 패치(또는 Catalog 승인 rollForward 범위) 충족. `8.0.100`으로 `8.0.400` 요청 충족 불가(기본) |
| C++ Toolset | **exact**. v143만 있다고 v142 요청을 충족하지 않음 |
| Windows SDK | **exact**(기본). 프로젝트/조직 정책이 명시할 때만 대체 허용 |
| 사내 SDK / WiX / 커스텀 targets | 정확한 버전·설치 상태 비교 |

### 8.3 이미지 Resolve 절차 (Portal)

```text
1. Requested Profile 검증·정규화 → Build Input Profile → requestedProfileHash
2. reuseMode == exactReuse ?
     → Exact READY(또는 정책상 DEPRECATED)만 조회
3. 아니면:
     a. Exact READY (requestedProfileHash) 있으면 → matchType=EXACT
     b. 없으면 Capability Matcher:
        candidates = READY(+정책상 DEPRECATED)
          .where(workerCompatible)
          .where(msbuildCompatible)
          .where(requestedCapabilities ⊆ imageCapabilities)
        필수 Capability 하나라도 누락 → 제외
        점수 계산 후 best 선택 → matchType=COMPATIBLE_SUPERSET
     c. 후보 없음 → requestedProfileHash로 신규 생성 Lock
4. build_request에 requested/matched hash, matchType, digest 기록
```

의사 코드:

```text
if reuseMode == exactReuse:
  img = find_exact(requestedHash)
  return img or create(requestedHash)

img = find_exact(requestedHash)
if img: return EXACT(img)

candidates = filter_capability_subset(READY_images, requested)
if candidates:
  return COMPATIBLE_SUPERSET(best_score(candidates))

return create(requestedHash)  # CREATING lock on requestedHash
```

### 8.4 선택 우선순위·점수

요청을 충족하는 이미지가 여러 개면:

1. Exact 일치
2. 불필요한 추가 Capability가 가장 적은 이미지
3. 현재 승인(Hot/비 Deprecated) 이미지
4. 보안 업데이트가 적용된 최신 이미지
5. Windows Worker에 이미 캐시된 이미지
6. 이미지 크기가 작은 이미지
7. 최근 검증 성공 이미지

```text
matchScore =
  + 권장 버전 일치 점수
  - 불필요 Capability 수
  - Deprecated 패널티
  - 이미지 크기 패널티
  + Worker 캐시 점수
  + Hot bonus
```

### 8.5 Superset 부작용 정책

여분 Toolset/SDK가 빌드 결과에 영향을 줄 수 있으면:

- 기본 UI/빌드는 `preferCompatible` 허용 (Preset Hot 재사용이 목적)
- **릴리스 재현·감사 빌드**는 `exactReuse: true`
- Catalog에 `sideEffectCapabilities`를 두고, 해당 여분이 있는 이미지는 Compatible 매칭에서 제외하거나 경고 후 확인
- 최종 빌드 기록에 `requestedProfileHash`, `matchedProfileHash`, `matchType`, `extraCapabilities`를 모두 남김

### 8.6 CREATING 중 Superset 대기

초기 정책: **Exact Hash의 CREATING waiter만** 공유 대기한다.  
다른 요청이 “만들어지고 있는 더 큰 이미지”를 기다리며 Capability 매칭하는 동작은 2단계 이후 선택 기능으로 둔다(복잡도·취소 레이스 회피).

---

## 9. 데이터 모델

### 9.1 `build_component_catalog`

```text
id, component_type, component_key, display_name, version,
visual_studio_generation, windows_base, installer_sha256,
layout_manifest_digest, enabled, administrator_only, sort_order
```

### 9.2 `build_profile`

요청/생성 입력 Profile(Build Input).

```text
id, profile_hash (UNIQUE), normalized_profile_json,
canonical_json, display_name, catalog_version,
created_by, created_at
```

### 9.3 `build_image`

```text
id, profile_hash (UNIQUE among active rows),
image_repository, image_tag, image_digest, status,
base_image_digest, windows_base, vs_generation,
capability_profile_json, validation_result_json,
factory_job_id, lease_owner, lease_id, lease_expires_at,
retry_count, failure_code, catalog_version,
created_at, updated_at, last_used_at, ready_at
```

`status`:

```text
CREATING | VALIDATING | READY | FAILED | DEPRECATED | QUARANTINED | DELETED
```

부분 Unique 권장:

```sql
CREATE UNIQUE INDEX build_image_profile_hash_active_uidx
  ON build_image (profile_hash)
  WHERE status NOT IN ('DELETED');
```

DELETED 후 동일 hash 재생성은 새 행 INSERT 또는 행 부활(상태 전이) 중 하나로 통일한다. 기본은 **행 부활(CAS)**.

### 9.4 `build_image_capability`

⊆ 검색용 정규화 테이블.

```text
id, build_image_id, capability_key, capability_version,
UNIQUE(build_image_id, capability_key, capability_version)
INDEX(capability_key, capability_version)
```

### 9.5 `build_request`

```text
id, repository, git_ref, resolved_commit, solution_path,
configuration, platform,
requested_profile_hash, matched_profile_hash, match_type,
reuse_mode, image_digest,
nuget_mode, jenkins_job_name, jenkins_queue_id, jenkins_build_number,
status, requested_by, requested_at, finished_at,
error_code, error_message,
idempotency_key (UNIQUE per user, nullable)
```

`match_type`:

```text
EXACT | COMPATIBLE_SUPERSET | CREATED | PENDING
```

### 9.6 `build_event`

```text
id, build_request_id, event_type, message, metadata_json, created_at
```

SSE는 `id`를 이벤트 ID로 사용해 `Last-Event-ID` 재개를 지원한다.

### 9.7 인덱스·제약

```text
UNIQUE(build_profile.profile_hash)
UNIQUE partial(build_image.profile_hash) WHERE status <> DELETED
INDEX(build_request.status, requested_profile_hash)
INDEX(build_image.status, last_used_at, windows_base)
INDEX(build_image_capability.capability_key, capability_version)
```

---

## 10. 동시성 Lock과 이미지 생성

### 10.1 목표

동일 `requested_profile_hash`에 대해 **이미지 빌드는 최대 1개**. Exact waiter는 결과를 공유한다.  
Capability Superset은 **이미 READY인 이미지**만 대상으로 한다.

### 10.2 획득 절차 (Portal)

```text
BEGIN;
-- Exact 경로
SELECT * FROM build_image WHERE profile_hash = $h
  AND status <> 'DELETED' FOR UPDATE;

없으면:
  INSERT status=CREATING, lease_owner, lease_id, lease_expires_at=now()+TTL
  → Factory Job 트리거
  → request: IMAGE_BUILD_QUEUED, match_type=PENDING

있으면 CREATING|VALIDATING:
  → request: IMAGE_WAITING (Exact waiter)

있으면 READY:
  → EXACT 연결, BUILD_QUEUED

있으면 FAILED 이고 retry 가능:
  → CREATING으로 CAS (lease_id 갱신) 후 재큐

있으면 DEPRECATED (재현/정책 허용):
  → Exact와 동일하게 digest 사용 가능

있으면 QUARANTINED:
  → 거부
COMMIT;

-- Exact 미충족이고 preferCompatible 이면
-- READY 이미지에 대해 Capability Matcher (읽기 위주, FOR UPDATE 불필요)
-- 후보 없으면 위의 CREATE 경로로 진입
```

대안: PostgreSQL advisory lock (`hashtextextended(profile_hash, 0)`) + Unique 제약.

### 10.3 Lease / Callback CAS / Reconcile

| 상황 | 동작 |
|------|------|
| Factory 정상 완료 | VALIDATING → (final push) → READY, capability 기록, Exact waiter BUILD_QUEUED |
| Factory 실패 | FAILED, lease 해제, retry_count+1 |
| Lease TTL 초과 | Reconciliation이 실상태 조회 후 FAILED 또는 lease 연장 |
| Callback 유실 | Factory 결과·Registry digest로 보정 |
| **Stale Callback** | Callback `leaseId` ≠ 현재 `lease_id`이면 **무시** + 감사 로그 |

권장 TTL: 예상 상한의 1.5배. Heartbeat로 연장.

### 10.4 Waiter / Cancel

- SSE: `IMAGE_BUILDING` / `IMAGE_VALIDATING` / `READY` / `FAILED` (+ matchType)
- 사용자 취소 → request만 `CANCELLED`
- Factory 취소: Exact waiter 재집계가 0일 때만, **짧은 grace 후 재확인** 뒤 취소
- Jenkins 트리거: `build_request_id`를 idempotency key로 중복 큐잉 방지
- Waiter 일깨우기: `UPDATE ... WHERE status='IMAGE_WAITING' RETURNING` 원자적 처리

---

## 11. Build Request 상태 머신

### 11.1 누가 무엇을 하나

| 상태 | 주체 | 설명 |
|------|------|------|
| REQUESTED | Portal | API 접수, idempotency, commit resolve |
| VALIDATING_PROFILE | Portal (동기) | Catalog·Allow-list·워커 존재 |
| RESOLVING_IMAGE | Portal | Exact → Capability Matcher → Lock |
| IMAGE_BUILD_QUEUED | Portal | Factory 트리거됨 |
| IMAGE_BUILDING | Factory Callback | 빌드 중 |
| IMAGE_VALIDATING | Factory Callback | Smoke/샘플 |
| IMAGE_WAITING | Portal | 동일 hash 생성 대기 |
| BUILD_QUEUED | Portal | Jenkins Project Build 트리거 |
| BUILDING / TESTING / PUBLISHING | Jenkins Callback | 프로젝트 파이프라인 |
| SUCCEEDED | Jenkins Callback | 완료 |

실패:

```text
PROFILE_REJECTED
IMAGE_BUILD_FAILED
IMAGE_VALIDATION_FAILED
PROJECT_BUILD_FAILED
TEST_FAILED
CANCELLED
NO_COMPATIBLE_WORKER
```

### 11.2 전이 다이어그램

```text
REQUESTED
    │
    ▼
VALIDATING_PROFILE
    │
    ▼
RESOLVING_IMAGE
    ├─ EXACT READY/DEPRECATED ──────────────► BUILD_QUEUED
    ├─ COMPATIBLE_SUPERSET READY ───────────► BUILD_QUEUED
    ├─ Exact CREATING(타 요청) ─────────────► IMAGE_WAITING ──► BUILD_QUEUED
    └─ 후보 없음/재시도 ────────────────────► IMAGE_BUILD_QUEUED
                                                ▼
                                          IMAGE_BUILDING
                                                ▼
                                          IMAGE_VALIDATING
                                                ▼
                                     READY + capability 저장
                                                ▼
                                            BUILD_QUEUED
                                                ▼
                                   BUILDING → TESTING → PUBLISHING → SUCCEEDED
```

**중요:** `VALIDATING_PROFILE`은 Jenkins 진입 전 Portal에서 끝낸다.

### 11.3 이미지 상태 ↔ 요청 상태 매핑

| build_image.status | build_request.status (대표) |
|--------------------|-----------------------------|
| CREATING | IMAGE_BUILD_QUEUED / IMAGE_BUILDING / IMAGE_WAITING |
| VALIDATING | IMAGE_VALIDATING / IMAGE_WAITING |
| READY | BUILD_QUEUED 이후 |
| FAILED | IMAGE_BUILD_FAILED / IMAGE_VALIDATION_FAILED |

---

## 12. 비동기 실행 시나리오

### 12.1 사용자 요청

```json
{
  "project": {
    "repository": "ProductClient",
    "gitRef": "release/2.1",
    "solutionPath": "ProductClient.sln",
    "configuration": "Release",
    "platform": "x64"
  },
  "environment": {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": ["8.0"],
    "cppToolsets": [],
    "windowsSdks": [],
    "features": ["managed-desktop"],
    "reuseMode": "preferCompatible"
  },
  "nuget": {
    "mode": "repo-packages-and-internal-feed"
  }
}
```

### 12.2 Portal

1. SSO·권한·Idempotency-Key 확인  
2. Profile 검증·정규화·Canonical Hash  
3. `resolved_commit` 고정, `build_request` 생성  
4. Exact → Capability Matcher → (필요 시) Factory Lock  
5. READY면 Jenkins `msbuild-project-build`만 트리거  

### 12.3 Exact / Superset 재사용

```text
RESOLVING_IMAGE → (EXACT | COMPATIBLE_SUPERSET) → BUILD_QUEUED → … → SUCCEEDED
감사: requestedHash, matchedHash, matchType, extraCapabilities
```

### 12.4 이미지 없는 경우 (비동기)

```text
Portal                Factory                 Jenkins
  │                      │                       │
  ├─ CREATING / queue ──►│                       │
  │◄── HEARTBEAT(lease)──┤                       │
  │◄── VALIDATING ───────┤                       │
  │◄── READY+digest+caps─┤                       │
  ├─ Exact waiter BUILD_QUEUED ─────────────────►│
  │◄────────────── BUILD events ─────────────────┤
```

---

## 13. Jenkins Job 구조

### 13.1 Job: `msbuild-project-build`

파라미터: `BUILD_REQUEST_ID`

```text
Portal에서 request·digest·nodeSelector 조회
Windows Kubernetes Pod 생성 (digest)
Checkout resolved_commit
NuGet restore (정책 모드)
MSBuild / Test / Publish
Portal Callback
```

### 13.2 Job: `msbuild-image-factory`

파라미터: `BUILD_REQUEST_ID`, `PROFILE_HASH`, `FACTORY_LEASE_ID`

```text
Build Input Profile 조회
Dockerfile·.vsconfig 생성
Windows 이미지 빌드 → staging 태그
Smoke Test + Capability 실측
성공 시 final 리포지토리로 Push
Portal Callback: digest + capability_profile + leaseId
실패 시 staging GC + FAILED Callback
```

### 13.3 (선택) 과도기 Orchestrator

Ensure Image는 **비동기 폴링/재큐**만. `wait: true` 금지.

---

## 14. 주문형 이미지 생성

### 14.1 공통 Agent 베이스

```text
agent-base-ltsc2019 / agent-base-ltsc2022
├─ Windows / .NET Framework Runtime
├─ JRE, Git, PowerShell
├─ Jenkins Agent 요구 환경
├─ 사내 CA 인증서
└─ 공통 빌드 스크립트
```

### 14.2 파생 Dockerfile

```dockerfile
# escape=`

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

SHELL ["cmd", "/S", "/C"]

COPY profile.vsconfig C:\ImageBuild\profile.vsconfig
COPY scripts C:\ImageBuild\scripts

RUN C:\ImageBuild\scripts\Install-BuildEnvironment.cmd

RUN powershell.exe -NoProfile -NonInteractive `
    -File C:\ImageBuild\scripts\Validate-Environment.ps1

LABEL company.build.profile-hash="83c87a10..."
LABEL company.build.template-version="image-template-3"
LABEL company.build.layout-release="vs2022-17.14.x"
```

Offline Layout은 **빌드 컨텍스트 COPY 대신 RO 마운트/캐시 볼륨**으로 공급한다. 경로·체크섬은 Catalog에 고정한다.

신규 Cold 이미지는 **요청 Capability 최소 집합만 설치**하는 것이 기본이다. 넓은 번들은 Hot Preset으로만 사전 생성한다.

---

## 15. 이미지 검증과 Capability 기록

생성 직후 바로 final READY로 두지 않는다.

1. staging 이미지에서 `vswhere`, `MSBuild -version`, `dotnet --list-sdks`
2. Targeting Pack / Windows SDK 경로 존재
3. `cl.exe` 등 C++ 도구 (해당 Profile만)
4. Profile별 샘플 솔루션 빌드
5. **실측 Capability Profile JSON 생성**

성공 시에만 final 리포지토리에 digest 게시 → `capability_profile_json` / `build_image_capability` 저장 → `READY`.  
실패 시 staging GC, `FAILED` 또는 `QUARANTINED`.

---

## 16. Kubernetes Windows Pod와 운영 제약

### 16.1 Pod 스펙 요지

```yaml
apiVersion: v1
kind: Pod
spec:
  os:
    name: windows
  nodeSelector:
    kubernetes.io/os: windows
    build.company.io/windows-release: ltsc2022
    build.company.io/purpose: msbuild
  tolerations:
    - key: build.company.io/windows
      operator: Equal
      value: "true"
      effect: NoSchedule
  containers:
    - name: builder
      image: registry.internal/build/msbuild-profile@sha256:...
      imagePullPolicy: IfNotPresent
      workingDir: C:\workspace
      resources:
        requests:
          cpu: "2"
          memory: 4Gi
          ephemeral-storage: 20Gi
        limits:
          cpu: "8"
          memory: 16Gi
          ephemeral-storage: 80Gi
  imagePullSecrets:
    - name: internal-registry-secret
```

### 16.2 필수 운영 요구사항

| 항목 | 요구 |
|------|------|
| OS 호환 | 컨테이너 LTSC ↔ 노드 LTSC 매칭. process/Hyper-V 정책을 노드 풀별 문서화 |
| 디스크 | 프로필 이미지 40–80Gi+ 레이어. ephemeral-storage 모니터링 |
| Pull SLA | Cold Pull 목표(예: 15–30분). Hot Pre-pull |
| Factory 용량 | 전용 노드, 동시 빌드 1–2. 큐잉은 Portal |
| Layout 공급 | 공유 저장소 가용성·체크섬 |
| 라이선스 | VS Build Tools 컨테이너 정책 법무/구매 합의 |
| 네트워크 | 내부 Registry DNS, 사내 CA, NuGet 피드 |

Portal은 요청 검증 시 해당 `windows-release` 노드 Ready 여부를 확인한다. 없으면 `NO_COMPATIBLE_WORKER`.

---

## 17. NuGet / 폐쇄망 Restore

빌드 Pod는 인터넷에 나가지 않는다.

### 17.1 지원 모드

| mode | 설명 |
|------|------|
| `repo-packages` | 저장소 `packages/` 또는 vendored assets만 |
| `internal-feed` | 사내 NuGet 피드(HTTPS + 사내 CA) |
| `repo-packages-and-internal-feed` | 둘 다 (권장 기본값) |

`NuGet.config`는 프로젝트 포함본 또는 Portal 승인 템플릿만. 임의 외부 URL 거부.  
자격증명은 Jenkins Credentials → Pod 파일/환경변수로 주입하고 로그 마스킹한다.

### 17.2 파이프라인

```text
checkout(resolved_commit)
→ nuget/dotnet restore (offline/internal only)
→ msbuild
→ test
→ publish artifacts (bin, binlog, test results)
```

---

## 18. Portal API

### 18.1 선택 항목 조회

```http
GET /api/v1/build-environment/options
```

응답에 `presets`, `visualStudios[].allowed`, `compatibilityRules`(단일 DSL), `estimatedImageBuildMinutes`, `capabilityMatching` 정책을 포함한다.

### 18.2 Profile 사전 검증·매칭 미리보기

```http
POST /api/v1/build-environment/validate
```

```json
{
  "valid": true,
  "requestedProfileHash": "83c87a10f35b...",
  "matchedProfileHash": "aa11bb22cc33...",
  "matchType": "COMPATIBLE_SUPERSET",
  "imageStatus": "READY",
  "action": "REUSE_COMPATIBLE",
  "estimatedWaitMinutes": 0,
  "providedCapabilities": [
    "vs2022",
    "net48-targeting-pack",
    "dotnet-sdk-8.0",
    "managed-desktop",
    "cpp-v143",
    "mfc"
  ],
  "extraCapabilities": ["cpp-v143", "mfc"],
  "warnings": []
}
```

`action` 예: `REUSE_EXACT` | `REUSE_COMPATIBLE` | `IMAGE_CREATION_REQUIRED` | `REJECTED`.

### 18.3 빌드 요청 / 상태 / SSE

```http
POST /api/v1/build-requests
GET  /api/v1/build-requests/{id}
GET  /api/v1/build-requests/{id}/events   # SSE, Last-Event-ID
```

`POST`는 `Idempotency-Key` 헤더를 지원한다.

이미지 READY 응답 예:

```json
{
  "status": "READY",
  "requestedProfileHash": "request-hash...",
  "matchedProfileHash": "image-hash...",
  "matchType": "COMPATIBLE_SUPERSET",
  "image": {
    "repository": "registry.internal/build/msbuild-profile",
    "tag": "vs2022-net48-dotnet8-v143-83c87a10",
    "digest": "sha256:..."
  },
  "reused": true,
  "providedCapabilities": ["vs2022", "net48-targeting-pack", "dotnet-sdk-8.0", "managed-desktop"],
  "extraCapabilities": ["cpp-v143"]
}
```

Exact:

```json
{
  "status": "READY",
  "requestedProfileHash": "83c87a10f35b...",
  "matchedProfileHash": "83c87a10f35b...",
  "matchType": "EXACT",
  "reused": true
}
```

신규 생성 후:

```json
{
  "status": "READY",
  "requestedProfileHash": "83c87a10f35b...",
  "matchedProfileHash": "83c87a10f35b...",
  "matchType": "CREATED",
  "reused": false
}
```

### 18.4 Internal Callback (MVP 필수 인증)

```http
POST /internal/v1/build-events
POST /internal/v1/images/{profileHash}/status
```

인증 (하나 이상 필수): mTLS 또는 HMAC-SHA256(`X-Timestamp` + body, 재생 방지 5분) + IP allow-list(보조).

Callback 예:

```json
{
  "requestId": "br-20260715-000142",
  "eventType": "IMAGE_READY",
  "message": "이미지 검증 완료 및 final push",
  "leaseId": "factory-run-98",
  "imageDigest": "sha256:...",
  "capabilityProfile": {
    "visualStudio": "2022",
    "dotnetFrameworks": ["4.8"],
    "dotnetSdks": [{ "version": "8.0.412" }],
    "cppToolsets": [],
    "windowsSdks": [],
    "features": ["managed-desktop"],
    "windowsBase": "ltsc2022"
  }
}
```

Portal은 `leaseId` CAS로만 상태 전이한다. Reconciliation Worker가 유실을 보정한다.

---

## 19. 보안 정책

### 19.1 사용자 제공 불가

```text
임의 Dockerfile / RUN / PowerShell
임의 Installer 경로 / 외부 URL
Registry Push 대상 / Image Tag
Kubernetes Node Selector
NuGet 임의 피드 URL
```

### 19.2 사용자 허용

```text
승인된 VS 세대, Targeting Pack, .NET SDK, C++ Toolset, Windows SDK, 기능
reuseMode, 프로젝트 빌드 파라미터, 승인된 NuGet mode
```

### 19.3 권한 분리

```text
Build Portal     → Factory 큐잉, Jenkins 트리거, SSO/RBAC
Project Build    → 이미지 Pull, Artifact 업로드
Image Factory    → 이미지 Build/Push, Offline Layout 읽기
```

### 19.4 MVP 보안 최소선

- Portal SSO + RBAC (일반 / 고급 / 관리자 / exactReuse 권한)
- Internal Callback HMAC 또는 mTLS + lease CAS
- Registry pull/push 계정 분리
- 감사 로그: 누가 어떤 requested/matched profileHash·digest로 빌드·이미지를 만들었는지

이미지 서명·SBOM은 고도화 단계에서 추가하되, digest 고정은 전 단계에서 강제한다.

---

## 20. 이미지 수명주기와 조합 폭발 통제

### 20.1 상태

```text
READY        → Exact·Compatible 재사용 가능
DEPRECATED   → 신규 UI 선택 불가, 재현/정책 매칭만
QUARANTINED  → 매칭·실행 금지
DELETED      → Registry 제거 (감사 기간 후)
```

### 20.2 Hot / Cold

```text
Hot Profile  → 넓은 Capability Preset, 사전 생성·Pre-pull (Superset 재사용 핵심)
Cold Profile → 요청 최소 집합만 생성, 미사용 시 캐시/GC
```

Capability 매칭이 있으므로 Cold 조합 폭발을 **Hot Preset 재사용으로 흡수**하는 것이 운영 전략의 중심이다.

### 20.3 쿼터·GC

| 정책 | 기본값 |
|------|--------|
| 동시 CREATING 전역 | Factory 슬롯 (예: 2) |
| 사용자당 Cold 신규 / 일 | 예: 3 (관리자 제외) |
| 고급 조합 | 관리자 승인 또는 Allow-list PR |
| Registry 보존 | 예: 180일 |
| GC 후보 | `last_used_at` 경과 + Hot 아님 + READY/DEPRECATED |
| 노드 디스크 알람 | 사용률 80% |

### 20.4 agent-base 갱신과 Blast Radius

`agentBase.digest`가 Build Input Hash에 포함되므로 JRE/Agent/CA 갱신은 **새 Exact Hash**를 만든다.  
기존 이미지의 Capability는 유지되므로, **보안 패치 전**에는 Compatible 매칭이 구 digest를 고를 수 있다.

롤아웃:

1. 새 `agent-base` 병행 게시  
2. Catalog 전환 → 신규 Exact/생성만 새 Hash  
3. Hot Preset 배치 재빌드·Pre-pull  
4. 구 이미지 DEPRECATED → Compatible 매칭 패널티 또는 제외  
5. 긴급 패치: QUARANTINE + 일괄 재빌드  

릴리스 트랙별 `agentBasePin` / Catalog freeze를 지원한다.

---

## 21. 인터넷망에서 준비할 산출물

```text
base-images/
visual-studio-layouts/          # + layout manifest checksums
installers/                     # SDK, Targeting Pack, Windows SDK, 사내 SDK…
image-factory/                  # templates, catalog, scripts, validation projects
jenkins/
manifest/                       # checksums, digests, release metadata
tests/fixtures/profile-hash/
tests/fixtures/capability-match/
```

---

## 22. 단계별 구현 순서

### 22.1 0단계: 기반 계약 (완료 게이트)

```text
Canonical Profile Hash + fixture
Capability Profile 스키마 + ⊆ Matcher fixture
CREATING Lock + lease CAS + reconcile
Internal Callback 인증
Options API 단일 호환 DSL
Preset 이미지 사전 빌드·Pre-pull (Hot)
Windows 노드 풀·디스크·OS 호환 검증
```

### 22.2 1단계: MVP

```text
Web Portal + SSO/RBAC
고정 Preset 4개 READY만 사용
Exact Hash + Capability Superset 매칭
Jenkins Project Build + Windows Pod
NuGet internal/repo 모드
SSE 상태 (재개 지원)
matchType/requested/matched 감사 필드
```

초기 Preset: `managed-net46`, `managed-net48`, `managed-dotnet8`, `native-v143-mfc`  
이 단계에서는 **주문형 Factory를 열지 않는다.** 매칭 실패 시 거부 또는 관리자 티켓.

**1단계 완료 조건:** Preset Pre-pull 성공 + OS 호환 검증 + Matcher가 Preset에 대해 EXACT/COMPATIBLE을 올바르게 반환.

### 22.3 2단계: 주문형 Image Factory

```text
미존재 시 최소 Capability 이미지 생성
Offline Layout RO 마운트, staging 검증, final Push
Capability 실측 DB 기록
중복 생성 Lock, Exact waiter SSE, 실패 재시도
```

### 22.4 3단계: 고급 사용자 선택

```text
개별 Targeting Pack / SDK / Toolset / Windows SDK / MFC·ATL·C++/CLI
일일 쿼터·승인 게이트
sideEffectCapabilities 정책
exactReuse 재현 모드 UI
```

### 22.5 4단계: 운영 고도화

```text
Deprecated/Quarantine, agent-base 롤아웃 자동화
Worker Pre-pull, 사용량 GC
SBOM·이미지 서명·Audit
CREATING 중 Superset 대기(선택)
Portal 실시간 로그 연동
```

---

## 23. 최종 권장 구조

```text
사용자
→ Web에서 승인된 빌드 도구 선택 (reuseMode)

Build Portal (Source of Truth)
→ 선택 검증 · Build Input 정규화 · Canonical Hash
→ Exact Resolve → Capability Matcher → (필요 시) Create Lock
→ 상태 머신 · lease CAS Callback

충족 이미지 READY (EXACT | COMPATIBLE_SUPERSET)
→ Jenkins가 digest로 Windows Pod 빌드
→ 감사: requestedHash + matchedHash + matchType + digest

후보 없음
→ 전용 Image Factory가 요청 최소 Capability로 1회 생성·검증·Push
→ 실측 Capability 저장
→ Exact waiter 공유 후 Project Build

Windows Build Pod
→ Checkout(resolved_commit) → 승인 NuGet → MSBuild/Test → Artifact → Pod 삭제
```

### 23.1 개정 2 핵심 차이점

1. **재사용 기준:** Hash 일치 ⊂이 아니라 **Capability 포함 관계(⊆)**. Hash는 식별·Lock·감사.  
2. **Profile 3종:** Requested / Capability / Build Input.  
3. **설치 원본 완전 핀** + Layout manifest digest.  
4. **검증 후 final push** + 실측 Capability DB.  
5. **leaseId CAS**, waiter/cancel 원자성, staging/final 분리.  
6. **Hot Preset + Superset 매칭**으로 조합 폭발을 운영적으로 흡수.  
7. Factory는 **전용 Windows 호스트**, Project Build와 권한·노드 분리.

핵심 구현 원칙(도구 선택 → 시스템 Profile → 폐쇄망 재사용 빌드)은 유지한다. 개정 2의 차이는 **“같은 이미지인가”가 아니라 “이 이미지로 빌드 가능한가”를 Resolve의 1급 계약으로 올린 것**이다.
