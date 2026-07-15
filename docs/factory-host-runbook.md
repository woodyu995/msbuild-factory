# Windows Image Factory Host Runbook

이 문서는 Portal이 큐잉한 `msbuild-image-factory` Job을 실행할 **전용 Windows 호스트** 준비 절차다.

## 1. 역할

- Offline Layout / 독립 installer로 MSBuild 프로필 이미지 생성
- staging 검증 후 final Registry push
- Portal에 HMAC Callback (`READY` / `FAILED`) + lease heartbeat

프로젝트 빌드 Pod와 **노드·계정을 분리**한다.

## 2. 최소 호스트 요구

| 항목 | 권장 |
|------|------|
| OS | Windows Server 2019/2022 (컨테이너 호스트) |
| Docker | Windows containers 모드 |
| Disk | 여유 300Gi+ (레이아웃 + 이미지 레이어) |
| Label | Jenkins `msbuild-factory` |
| Network | 내부 Registry, Portal, Layout SMB/NFS |

## 3. 반입물 마운트

```text
IMAGE_FACTORY_LAYOUT_ROOT=\\storage\vs-layouts
IMAGE_FACTORY_INSTALLER_ROOT=\\storage\installers
```

Catalog의 `layoutRelease` / `installerSha256`와 실제 파일이 일치해야 한다.

## 4. Portal 연동

Jenkins credentials:
- `portal-base-url`
- `portal-callback-hmac` (= Portal `PORTAL_CALLBACK_HMAC_SECRET`)

Agent 스크립트:
- `jenkins/shared-library/scripts/portal_factory_agent.py`

로컬 dry-run (Linux/CI에서도 상태머신 검증 가능):

```bash
# Portal 기동 후 cold request로 CREATING lease 확보
python jenkins/shared-library/scripts/portal_factory_agent.py dry-run-all \
  --portal-url http://127.0.0.1:8000 \
  --hmac-secret dev-callback-secret-change-me \
  --profile-hash <PROFILE_HASH> \
  --lease-id <FACTORY_LEASE_ID> \
  --request-id <BUILD_REQUEST_ID> \
  --work-dir /tmp/factory-work
```

`fetch` / `factory-artifacts` / `heartbeat` / `finalize` 는 모두 HMAC 서명 + active `leaseId` 가 필요하다.
(`PORTAL_HMAC_SECRET` env만 있어도 agent가 사용한다.)

## 5. 빌드 순서

1. `fetch` — Dockerfile / vsconfig / install-manifest
2. `heartbeat` — lease 연장
3. `build` — Docker build (실호스트) 또는 `--dry-run`
4. `finalize` — READY + capabilityProfile Callback  
   실패 시 `fail`

### 실빌드 (non-dry-run) → Nexus push

```bash
export IMAGE_FACTORY_LAYOUT_ROOT=\\storage\vs-layouts
export IMAGE_FACTORY_INSTALLER_ROOT=\\storage\installers
export FACTORY_DRY_RUN=0
export NEXUS_REGISTRY_HOST=nexus.company.io:8082
# Jenkins provides NEXUS_DOCKER_USER / NEXUS_DOCKER_PASSWORD via nexus-docker credential

python jenkins/shared-library/scripts/portal_factory_agent.py build \
  --portal-url https://portal.internal \
  --hmac-secret "$PORTAL_HMAC" \
  --profile-hash <PROFILE_HASH> \
  --lease-id <LEASE_ID> \
  --request-id <BUILD_REQUEST_ID> \
  --work-dir D:\factory-work\<PROFILE_HASH>
```

요구사항: Docker Engine(Windows containers), **Nexus docker login**, Layout/Installer 경로 존재.  
`build`는 staging tag로 `docker build` → final tag → `docker login` → `docker push` → RepoDigest 수집 후 `result.json`에 기록한다.

Portal `PORTAL_REGISTRY_HOST` / `PORTAL_REGISTRY_FINAL_REPO` 가 artifacts의 repository 경로를 결정한다.

## 6. OS 호환

| 이미지 base | 노드 라벨 |
|-------------|-----------|
| ltsc2019 | `build.company.io/windows-release=ltsc2019` |
| ltsc2022 | `build.company.io/windows-release=ltsc2022` |

process/Hyper-V 격리 정책은 풀별로 문서화한다.

## 7. 보안 체크리스트

- [ ] Factory만 Registry **push** 가능
- [ ] Project build는 **pull**만
- [ ] Callback HMAC/mTLS 필수
- [ ] leaseId CAS (stale callback 거부)
- [ ] Offline Layout 읽기 전용

## 8. 운영 알람

- Factory 동시 CREATING > 슬롯(기본 2)
- lease TTL 임박 / reconcile `LEASE_EXPIRED`
- 노드 디스크 80%
- Cold pull SLA 초과
