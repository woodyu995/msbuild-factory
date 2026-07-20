# 폐쇄망 — 실제 Server Core + VS Build Tools 이미지 검증

목표: 포털에서 환경을 고르면 **Windows Docker**가 실제 베이스 위에서  
VS Build Tools를 설치한 이미지를 빌드하고, **Nexus 없이 로컬에 남긴 뒤** READY를 확인한다.

```text
[Linux] Portal (Ensure → CREATING + lease)
            ↓ HMAC
[Windows Factory] fetch → docker build (Server Core/agent-base + VS layout)
            → 로컬 tag + docker save (Nexus push 없음)
            → finalize → Portal READY
```

> Linux의 `PORTAL_LOCAL_FACTORY` stub(`FROM scratch`)로는 **이 목표를 달성할 수 없습니다.**  
> Windows 컨테이너 호스트가 필요합니다.

---

## 0. 필요한 것

| 구성 | 역할 |
|------|------|
| Linux Docker 호스트 | Portal만 기동 |
| **Windows Server** + Docker (**Windows containers**) | 실제 이미지 빌드 |
| USB 반입물 | 아래 표 |

### USB / 반입물

| 항목 | 설명 |
|------|------|
| `msbuild-portal.tar` | Portal 이미지 (인터넷망에서 빌드) |
| `msbuild-factory-src.tgz` | 소스 (agent 스크립트 포함) |
| **Windows base** | `mcr.microsoft.com/windows/servercore:ltsc2022` (또는 2019) tar |
| **VS Build Tools offline layout** | `vs_setup.exe` + 패키지 (`vs2022-17.14.x` 등 Catalog `layoutRelease`와 일치) |
| (선택) .NET SDK exe | `IMAGE_FACTORY_INSTALLER_ROOT` |

인터넷망에서 base 받기 예:

```bash
# Windows 머신(인터넷)에서
docker pull mcr.microsoft.com/windows/servercore:ltsc2022
docker save mcr.microsoft.com/windows/servercore:ltsc2022 -o servercore-ltsc2022.tar
```

VS layout은 Microsoft 오프라인 레이아웃 생성 절차로 미리 만들어 USB에 넣습니다.

---

## 1. 인터넷망 — Portal 이미지

```bash
git checkout cursor/portal-mvp-implementation-03e9
docker compose -f docker-compose.local.yml build
docker tag msbuild-factory-portal:latest msbuild-portal:airgap
docker save msbuild-portal:airgap -o msbuild-portal.tar
tar czf msbuild-factory-src.tgz --exclude .git --exclude local-images .
```

---

## 2. 폐쇄망 Linux — Portal 기동

```bash
docker load -i msbuild-portal.tar
tar xzf msbuild-factory-src.tgz && cd msbuild-factory

docker compose -f docker-compose.airgap-windows.yml up -d
curl -s http://127.0.0.1:8000/readyz
```

- 로그인 없음  
- Jenkins / Nexus 없음  
- HMAC 기본값: `airgap-dev-hmac-change-me` (Windows agent와 동일해야 함)

브라우저: `http://<linux-host>:8000/`

---

## 3. 폐쇄망 Windows — 베이스 준비

Windows containers 모드:

```powershell
docker load -i servercore-ltsc2022.tar

# Catalog가 기대하는 로컬 이름/태그 (MVP placeholder digest → tag 사용)
docker tag mcr.microsoft.com/windows/servercore:ltsc2022 msbuild-agent-base:ltsc2022

docker images msbuild-agent-base
```

레이아웃/인스톨러 경로 예:

```text
D:\vs-layouts\vs2022-17.14.x\vs_setup.exe   (또는 D:\vs-layouts\vs_setup.exe)
D:\installers\                               (비어 있어도 됨 — 폴더는 필요)
```

Python 3 + 이 저장소 소스(에이전트 스크립트)가 Windows에 있어야 합니다.

```powershell
$env:PORTAL_URL = "http://<linux-host>:8000"
$env:PORTAL_HMAC_SECRET = "airgap-dev-hmac-change-me"
$env:IMAGE_FACTORY_LAYOUT_ROOT = "D:\vs-layouts\vs2022-17.14.x"   # vs_setup.exe 있는 폴더
$env:IMAGE_FACTORY_INSTALLER_ROOT = "D:\installers"
$env:FACTORY_DRY_RUN = "0"
$env:FACTORY_SKIP_PUSH = "1"                  # Nexus 없음
$env:FACTORY_DOCKER_SAVE_DIR = "D:\factory-images"
$env:IMAGE_FACTORY_STRICT = "1"               # MSBuild 검증
```

---

## 4. 포털 Ensure → Windows에서 실빌드

### 4-1. UI

1. Reuse mode = **exactReuse**  
2. Cold 조합 (예: VS2022 + net48 + C++ v143 + WinSDK + MFC)  
3. **Ensure image** → 상태 **CREATING** (자동으로 READY 되면 안 됨 — Windows agent가 해야 함)

### 4-2. lease 확인

Linux에서:

```bash
# matchedProfileHash 는 UI 또는:
curl -s http://127.0.0.1:8000/api/v1/images/<HASH>
# factoryLeaseId 확인
```

### 4-3. Windows agent (Jenkins 없이)

```powershell
cd C:\msbuild-factory   # 소스 푼 경로
$HASH = "<matchedProfileHash>"
$LEASE = "<factoryLeaseId>"
$WORK = "D:\factory-work\$HASH"

python .\jenkins\shared-library\scripts\portal_factory_agent.py fetch `
  --portal-url $env:PORTAL_URL --hmac-secret $env:PORTAL_HMAC_SECRET `
  --profile-hash $HASH --lease-id $LEASE --work-dir $WORK

python .\jenkins\shared-library\scripts\portal_factory_agent.py build `
  --portal-url $env:PORTAL_URL --hmac-secret $env:PORTAL_HMAC_SECRET `
  --profile-hash $HASH --lease-id $LEASE --work-dir $WORK `
  --layout-root $env:IMAGE_FACTORY_LAYOUT_ROOT `
  --installer-root $env:IMAGE_FACTORY_INSTALLER_ROOT

python .\jenkins\shared-library\scripts\portal_factory_agent.py finalize `
  --portal-url $env:PORTAL_URL --hmac-secret $env:PORTAL_HMAC_SECRET `
  --profile-hash $HASH --lease-id $LEASE --work-dir $WORK
```

빌드 시간: VS offline 설치 포함 **수십 분 ~ 수 시간**, 디스크 **수백 GB** 여유 권장.

### 4-4. 결과 확인 (Windows)

```powershell
docker images msbuild-local/profile
Get-ChildItem D:\factory-images

# 이미지 안에서 MSBuild 확인 (예시)
docker run --rm msbuild-local/profile:<tag> cmd /c where msbuild
```

Portal UI는 **READY** + 실 digest (`sha256:dry-run-` / `simulated-` 아님).

---

## 5. 성공 기준

- [ ] Portal Ensure → CREATING  
- [ ] Windows `docker build`가 `msbuild-agent-base:ltsc2022`에서 시작  
- [ ] 레이아웃 `vs_setup.exe --noWeb`로 Build Tools 설치  
- [ ] Validate에서 MSBuild 발견  
- [ ] `FACTORY_SKIP_PUSH=1`로 로컬 tag (+ optional tar)  
- [ ] finalize 후 Portal READY  

---

## 6. 문제 해결

| 증상 | 조치 |
|------|------|
| `FROM` pull 실패 | `docker images msbuild-agent-base` — tag 일치 여부 |
| vs_setup 없음 | `IMAGE_FACTORY_LAYOUT_ROOT`에 exe 있는지 |
| MSBuild validation 실패 | 레이아웃/workload(.vsconfig) / 디스크 / 로그 |
| HMAC 401 | Portal `PORTAL_CALLBACK_HMAC_SECRET` = Windows `PORTAL_HMAC_SECRET` |
| CREATING 고정 | Windows에서 fetch/build/finalize 실행했는지 |
| Linux stub만 빌드됨 | `docker-compose.airgap-local.yml`이 아님 — **airgap-windows** 사용 |

---

## 다음에

- Nexus push: `FACTORY_SKIP_PUSH=0` + `NEXUS_*`  
- Jenkins `msbuild-factory` 에이전트 라벨 연결  
- k9s 배포  

관련: `docs/factory-host-runbook.md`, `jenkins/WIRING.md`
