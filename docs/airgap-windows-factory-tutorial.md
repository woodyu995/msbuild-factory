# 폐쇄망 튜토리얼 — Linux Docker + Windows Docker

폐쇄망에 **두 종류의 Docker 호스트**가 있다고 가정한다.

| 호스트 | Docker | 역할 |
|--------|--------|------|
| **Linux** | Linux containers | Portal UI/API만 실행 |
| **Windows** | **Windows containers** | Server Core + VS Build Tools 이미지 실빌드 |

```text
[사용자 브라우저] ──→ [Linux Docker] Portal :8000
                              │ Ensure → CREATING + leaseId
                              │ HMAC (내부망)
                              ▼
                     [Windows Docker] Factory
                         fetch → docker build → 로컬 저장
                         finalize → Portal READY
```

- Jenkins / Nexus / 로그인: **이 단계에서는 사용하지 않음**
- Linux의 stub 빌드(`docker-compose.airgap-local.yml`, `FROM scratch`)는 **쓰지 않음**

네트워크: Windows 호스트가 Linux Portal URL(`http://<linux-ip>:8000`)에 접근 가능해야 한다.

---

## 역할 분리 (중요)

| 작업 | 어디서 |
|------|--------|
| Portal compose up | **Linux** |
| 브라우저로 Ensure | 아무 PC → Linux:8000 |
| `servercore` load / tag | **Windows** |
| VS offline layout 두기 | **Windows** |
| `portal_factory_agent.py` build | **Windows** |
| `docker images`로 MSBuild 이미지 확인 | **Windows** |
| READY 상태 확인 | Portal UI (Linux) |

Linux Docker에는 Windows Server Core 이미지를 올리지 않는다.

---

## A. 인터넷망에서 USB 만들기 (한 번) — 상세

인터넷이 되는 PC **두 종류**가 있으면 편하다. 한 대에 둘 다 있으면 그 머신에서 순서대로 하면 된다.

| 인터넷망 머신 | 만드는 산출물 |
|---------------|----------------|
| **Linux 또는 Intel Mac** + Docker (Linux containers) | Portal 이미지 tar + 소스 tgz |
| **Windows** + Docker (**Windows containers**) | Server Core tar + (가능하면) VS layout |

Catalog 매핑 (반입물을 이 표에 맞출 것):

| Portal VS | Windows base | Catalog `layoutRelease` | 폐쇄망 Docker tag |
|-----------|--------------|-------------------------|-------------------|
| **2019** | `servercore:ltsc2019` | `vs2019-16.11.54` | `msbuild-agent-base:ltsc2019` |
| **2022** | `servercore:ltsc2022` | `vs2022-17.14.x` | `msbuild-agent-base:ltsc2022` |

둘 다 검증하려면 base·layout을 **2019 + 2022 각각** USB에 넣는다.  
예상 USB 용량: Portal 수 GB + Server Core×2 + layout×2 → **200GB+** 권장 (2022만이면 100GB+).

작업용 폴더 예: `~/airgap-export/` (Linux/Mac), `D:\airgap-export\` (Windows).

---

### A-0. 사전 확인

**Linux / Mac**

```bash
uname -m                    # Intel Mac이면 x86_64 권장
docker version
docker compose version
docker info --format '{{.OSType}} / {{.Architecture}}'
# 기대: linux / ...
git --version
```

**Windows (인터넷)**

```powershell
docker version
# OSType 이 windows 여야 함. linux면 "Switch to Windows containers"
docker info
# 디스크 여유: Server Core save + VS layout 생성에 충분해야 함
```

---

### A-1. 저장소 받기 (Linux / Mac)

```bash
mkdir -p ~/airgap-export && cd ~/airgap-export
git clone <repo-url> msbuild-factory
cd msbuild-factory
git checkout cursor/portal-mvp-implementation-03e9
git pull
git log -1 --oneline    # 나중에 폐쇄망에서 버전 대조용으로 적어 두기
```

브랜치가 다르면 Portal/agent 스크립트가 안 맞을 수 있다. **이 브랜치를 고정**한다.

---

### A-2. Portal 이미지 빌드 (Linux / Mac)

Portal은 **Linux 컨테이너**다. 인터넷에서 base(`node`, `python`)를 pull 하므로 **이 단계는 반드시 인터넷망**.

```bash
cd ~/airgap-export/msbuild-factory

# 빌드 (수 분 소요)
docker compose -f docker-compose.local.yml build

# 만들어진 이미지 이름 확인 (프로젝트 디렉터리명에 따라 prefix가 다를 수 있음)
docker images | grep -i portal

# 폐쇄망에서 쓸 고정 태그로 붙이기
docker tag msbuild-factory-portal:latest msbuild-portal:airgap
# 위 tag 가 실패하면 실제 이미지명을 넣어 다시:
# docker tag <실제이미지:태그> msbuild-portal:airgap

docker images msbuild-portal:airgap
```

실패 시:

| 증상 | 조치 |
|------|------|
| `no matching manifest` / windows | Docker가 Windows 모드 → **Linux containers**로 전환 |
| npm/pip pull 실패 | 인터넷/프록시 확인 |
| build 중 docker CLI 다운로드 실패 | Portal Dockerfile이 docker static CLI를 받음 — 인터넷 필요 |

---

### A-3. Portal tar + 소스 묶기 (Linux / Mac)

```bash
cd ~/airgap-export/msbuild-factory

# 1) Portal 이미지 저장 (수 GB)
docker save msbuild-portal:airgap -o ~/airgap-export/msbuild-portal.tar
ls -lh ~/airgap-export/msbuild-portal.tar

# 2) 소스/compose/agent 스크립트 (git 제외)
#    Linux·Windows 폐쇄망 둘 다에 풀어야 함
tar czf ~/airgap-export/msbuild-factory-src.tgz \
  --exclude .git \
  --exclude local-images \
  --exclude '**/__pycache__' \
  --exclude '**/node_modules' \
  --exclude '.venv' \
  -C ~/airgap-export \
  msbuild-factory

ls -lh ~/airgap-export/msbuild-factory-src.tgz
```

산출물 위치: `~/airgap-export/msbuild-portal.tar`, `msbuild-factory-src.tgz`.

---

### A-4. Windows Server Core 이미지 받기 (Windows)

폐쇄망 Windows Docker가 `FROM`으로 쓸 **베이스**. Linux에 load하지 않는다.  
**2019와 2022를 모두** 쓸 계획이면 둘 다 pull/save 한다.

```powershell
mkdir D:\airgap-export -Force
cd D:\airgap-export

# Windows containers 모드에서

# --- VS 2022 / ltsc2022 ---
docker pull mcr.microsoft.com/windows/servercore:ltsc2022
docker save mcr.microsoft.com/windows/servercore:ltsc2022 `
  -o D:\airgap-export\servercore-ltsc2022.tar

# --- VS 2019 / ltsc2019 ---
docker pull mcr.microsoft.com/windows/servercore:ltsc2019
docker save mcr.microsoft.com/windows/servercore:ltsc2019 `
  -o D:\airgap-export\servercore-ltsc2019.tar

Get-ChildItem D:\airgap-export\servercore-ltsc*.tar | Format-Table Name, Length
```

폐쇄망 Windows에서 retag (C 단계):

| tar | retag |
|-----|--------|
| `servercore-ltsc2019.tar` | `msbuild-agent-base:ltsc2019` |
| `servercore-ltsc2022.tar` | `msbuild-agent-base:ltsc2022` |

호스트 OS 호환: 일반적으로 **ltsc2019 이미지 → WS2019 호스트**, **ltsc2022 이미지 → WS2022 호스트**.  
한 Windows 호스트에서 둘 다 돌리려면 호스트/격리 모드가 허용하는지 미리 확인한다.

---

### A-5. VS Build Tools offline layout (Windows, 인터넷)

Catalog `layoutRelease`와 **폴더 이름을 동일**하게 만든다.

| VS | 다운로드 | layout 폴더명 |
|----|----------|----------------|
| **2019** | [Build Tools 2019](https://visualstudio.microsoft.com/vs/older-downloads/) (Build Tools) | `vs2019-16.11.54` |
| **2022** | [Build Tools 2022](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) | `vs2022-17.14.x` |

부트스트랩퍼(`vs_BuildTools.exe`)는 **연도별로 따로** 받는다. layout 폴더 안에 `vs_setup.exe` 또는 `vs_BuildTools.exe`가 있어야 한다.

> **주의 (MFC ID):** Build Tools 부트스트랩퍼는  
> `Microsoft.VisualStudio.Component.VC.MFC` 를 **인식하지 않는다** (IDE 전용 ID).  
> MFC는 반드시 **`Microsoft.VisualStudio.Component.VC.ATLMFC`** 를 쓴다.  
> ATL은 `Microsoft.VisualStudio.Component.VC.ATL`.

#### A-5a. VS 2022 layout

```powershell
mkdir D:\airgap-export\vs2022-17.14.x -Force
cd D:\airgap-export

# 2022 Build Tools 부트스트랩퍼 (파일명은 다운로드명에 맞게)
.\vs_BuildTools_2022.exe `
  --layout D:\airgap-export\vs2022-17.14.x `
  --lang en-US `
  --add Microsoft.VisualStudio.Workload.MSBuildTools `
  --add Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools `
  --add Microsoft.VisualStudio.Workload.VCTools `
  --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
  --add Microsoft.VisualStudio.Component.VC.ATLMFC `
  --add Microsoft.VisualStudio.Component.VC.ATL `
  --add Microsoft.VisualStudio.Component.Windows11SDK.22621 `
  --includeRecommended
```

#### A-5b. VS 2019 layout

**반드시 최신 2019 Build Tools 부트스트랩퍼**를 받는다 (오래된 exe는 `Windows10SDK.19041`을 모름):

```powershell
cd D:\airgap-export
# 공식 최신 채널 (vs/16 = VS 2019)
Invoke-WebRequest -Uri "https://aka.ms/vs/16/release/vs_buildtools.exe" `
  -OutFile ".\vs_BuildTools_2019.exe"
```

```powershell
mkdir D:\airgap-export\vs2019-16.11.54 -Force
cd D:\airgap-export

.\vs_BuildTools_2019.exe `
  --layout D:\airgap-export\vs2019-16.11.54 `
  --lang en-US `
  --add Microsoft.VisualStudio.Workload.MSBuildTools `
  --add Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools `
  --add Microsoft.VisualStudio.Workload.VCTools `
  --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
  --add Microsoft.VisualStudio.Component.VC.ATLMFC `
  --add Microsoft.VisualStudio.Component.VC.ATL `
  --add Microsoft.VisualStudio.Component.Windows10SDK.19041 `
  --includeRecommended
```

| 에러 | 원인 / 조치 |
|------|-------------|
| `Windows10SDK.19041` is not recognized | 부트스트랩퍼가 오래됨 → 위 `aka.ms/vs/16/release/vs_buildtools.exe`로 **다시 다운로드** 후 재실행 |
| 그래도 SDK ID 실패 | 임시로 `Windows10SDK.18362` 사용 가능하나, Portal Catalog 허용값(`10.0.19041.0`)과 어긋남 → 부트스트랩퍼 갱신을 우선 |

2019 Catalog 허용 범위 요약: netfx 4.6~4.8, C++ **v142**, WinSDK **10.0.19041.0**, features managed-desktop/mfc/atl (dotnet SDK 없음).

#### A-5c. layout 확인

```powershell
foreach ($dir in @(
  "D:\airgap-export\vs2019-16.11.54",
  "D:\airgap-export\vs2022-17.14.x"
)) {
  Write-Host "=== $dir ==="
  Get-ChildItem $dir -Filter "vs_*.exe" -ErrorAction SilentlyContinue |
    Select-Object FullName
}
```

폐쇄망 `IMAGE_FACTORY_LAYOUT_ROOT`는 **해당 연도 layout에서 setup exe가 있는 폴더**를 가리킨다.

- 연도당 수십 GB. 끊기면 같은 `--layout` 명령으로 재개되는 경우가 많다.  
- workload 누락 시 폐쇄망 MSBuild/MFC validation 실패.

---

### A-6. (선택) 독립 설치 파일

Portal에서 .NET SDK 등을 고르면 `IMAGE_FACTORY_INSTALLER_ROOT`에서 exe를 찾는다.

```powershell
mkdir D:\airgap-export\installers -Force
# 예: dotnet-sdk-8.0.xxx-win-x64.exe 를 이 폴더에 복사
```

Targeting Pack / WinSDK는 보통 VS layout workload에 포함시키면 된다.

---

### A-7. USB 폴더 구조 (권장)

USB 루트를 이렇게 맞추면 폐쇄망에서 헤매지 않는다.

```text
USB:\
  00-README.txt
  linux\
    msbuild-portal.tar
    msbuild-factory-src.tgz
  windows\
    servercore-ltsc2019.tar          # VS 2019용
    servercore-ltsc2022.tar          # VS 2022용
    msbuild-factory-src.tgz          # Linux와 동일 (agent용)
    vs-layouts\
      vs2019-16.11.54\               # VS 2019 layout (vs_setup.exe)
      vs2022-17.14.x\                # VS 2022 layout (vs_setup.exe)
    installers\                      # 비어 있어도 OK
```

`00-README.txt` 예:

```text
branch: cursor/portal-mvp-implementation-03e9
portal tag: msbuild-portal:airgap
hmac: airgap-dev-hmac-change-me
2019: servercore-ltsc2019 → msbuild-agent-base:ltsc2019 / layout vs2019-16.11.54
2022: servercore-ltsc2022 → msbuild-agent-base:ltsc2022 / layout vs2022-17.14.x
```

### USB에 넣을 목록 (요약)

| 파일/폴더 | 용량 감 | 폐쇄망에서 푸는 곳 |
|-----------|---------|-------------------|
| `msbuild-portal.tar` | 중 | **Linux** |
| `msbuild-factory-src.tgz` | 소 | **Linux + Windows** |
| `servercore-ltsc2019.tar` | 중 | **Windows** |
| `servercore-ltsc2022.tar` | 중 | **Windows** |
| `vs2019-16.11.54\` | **대** | **Windows** |
| `vs2022-17.14.x\` | **대** | **Windows** |
| `installers\` | 소~중 | **Windows** |

2022만 먼저 검증해도 되며. 다만 Portal에서 VS **2019**를 고르면 2019 base+layout이 필수다.

복사 후 USB에서 용량/`Get-FileHash`로 깨짐 여부를 한 번 확인하는 것을 권장한다.

---

## B. 폐쇄망 Linux 호스트 — Portal

```bash
docker load -i msbuild-portal.tar
tar xzf msbuild-factory-src.tgz
cd msbuild-factory

# 실제 Windows factory 연동용 compose (stub 아님)
docker compose -f docker-compose.airgap-windows.yml up -d
curl -s http://127.0.0.1:8000/readyz
```

- 로그인 없음  
- HMAC: `airgap-dev-hmac-change-me` (Windows와 **반드시 동일**)  
- Linux IP를 확인: `ip a` / `hostname -I` → 예 `10.0.0.10`

브라우저: `http://10.0.0.10:8000/`  
(Windows·사용자 PC에서 Linux IP로 접속)

Windows에서 연결 확인:

```powershell
curl http://10.0.0.10:8000/readyz
```

---

## C. 폐쇄망 Windows 호스트 — Factory 준비

1. Docker가 **Windows containers** 모드인지 확인  
2. 소스 압축 해제 (agent 스크립트용)  
3. **2019 / 2022 base** load + Catalog tag  
4. **연도별 layout** / installers 경로 준비  
5. Python 3 설치

```powershell
# --- bases ---
docker load -i servercore-ltsc2019.tar
docker load -i servercore-ltsc2022.tar
docker tag mcr.microsoft.com/windows/servercore:ltsc2019 msbuild-agent-base:ltsc2019
docker tag mcr.microsoft.com/windows/servercore:ltsc2022 msbuild-agent-base:ltsc2022
docker images msbuild-agent-base

# --- layouts (USB에서 복사한 경로 예) ---
# D:\vs-layouts\vs2019-16.11.54\vs_setup.exe
# D:\vs-layouts\vs2022-17.14.x\vs_setup.exe
# D:\installers\

tar xzf msbuild-factory-src.tgz -C C:\
cd C:\msbuild-factory   # 실제 푼 경로에 맞게
```

공통 환경 변수:

```powershell
$env:PORTAL_URL = "http://10.0.0.10:8000"
$env:PORTAL_HMAC_SECRET = "airgap-dev-hmac-change-me"
$env:IMAGE_FACTORY_INSTALLER_ROOT = "D:\installers"
$env:FACTORY_DRY_RUN = "0"
$env:FACTORY_SKIP_PUSH = "1"
$env:FACTORY_DOCKER_SAVE_DIR = "D:\factory-images"
$env:IMAGE_FACTORY_STRICT = "1"
```

빌드할 VS에 따라 layout 루트만 바꾼다:

```powershell
# VS 2022 빌드 전
$env:IMAGE_FACTORY_LAYOUT_ROOT = "D:\vs-layouts\vs2022-17.14.x"

# VS 2019 빌드 전
$env:IMAGE_FACTORY_LAYOUT_ROOT = "D:\vs-layouts\vs2019-16.11.54"
```

---

## D. 검증 시나리오 (양쪽 협력)

### D-1. Linux Portal UI에서 Ensure

1. `http://<linux-ip>:8000/` 접속  
2. Reuse mode = **exactReuse**  
3. Cold 조합 예:
   - **2022**: VS 2022 + net48 + C++ **v143** + WinSDK 22621 + MFC  
   - **2019**: VS 2019 + net48 + C++ **v142** + WinSDK **19041** + MFC  
4. **Ensure image**  
5. 상태가 **CREATING**인지 확인 (여기서 멈추는 것이 정상 — Windows가 빌드해야 함)

> Portal에서 고른 VS 연도와 Windows의 base tag / layout 루트가 **같아야** 한다.  
> 2019 Ensure인데 `ltsc2022` / `vs2022-…` layout만 있으면 빌드가 실패한다.

UI에 나온 `profile` / hash와, 필요하면 Linux에서 lease 확인:

```bash
curl -s http://127.0.0.1:8000/api/v1/images/<HASH>
# factoryLeaseId 사용
```

### D-1b. 만료 lease 즉시 복구 (재부팅 후)

`profile` hash는 그대로인데 `leaseExpiresAt`만 과거이면, **셸의 옛 `$LEASE`로는 fetch/build가 거절**된다.

**최신 Portal** (권장): UI Ensure 또는

```bash
curl -s -X POST "http://127.0.0.1:8000/api/v1/images/<HASH>/rearm-lease"
# → 새 factoryLeaseId + 미래 leaseExpiresAt
```

**구 Portal 이미지** (Ensure 해도 expires가 안 움직임): Linux에서 expire reconcile 후 Ensure

```bash
# HMAC = compose의 PORTAL_CALLBACK_HMAC_SECRET (기본 airgap-dev-hmac-change-me)
python3 - <<'PY'
import hashlib, hmac, json, time, urllib.request
secret = "airgap-dev-hmac-change-me"
body = b"{}"
ts = str(int(time.time()))
sig = hmac.new(secret.encode(), (ts.encode() + b"." + body), hashlib.sha256).hexdigest()
req = urllib.request.Request(
    "http://127.0.0.1:8000/internal/v1/reconcile/leases",
    data=body,
    headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig},
    method="POST",
)
print(urllib.request.urlopen(req).read().decode())
PY
```

그다음 UI에서 **같은 옵션 Ensure** → **새** `factoryLeaseId`를 `$LEASE`에 넣고 Windows 진행.

### D-2. Windows에서 실빌드

```powershell
cd C:\msbuild-factory
$HASH = "<UI의 matchedProfileHash>"
$LEASE = "<Ensure/rearm 직후의 factoryLeaseId>"   # 재부팅 전 값 재사용 금지
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

- 빌드: VS 설치 포함 **수십 분 ~ 수 시간**  
- 디스크: **수백 GB** 여유 권장  
- Windows Docker가 `msbuild-agent-base:ltsc2022` 위에서 `vs_setup --noWeb` 실행

빌드를 포기하고 슬롯을 비우려면 (UI에 `factory slots full`일 때):

```powershell
python .\jenkins\shared-library\scripts\portal_factory_agent.py fail `
  --portal-url $env:PORTAL_URL --hmac-secret $env:PORTAL_HMAC_SECRET `
  --profile-hash $HASH --lease-id $LEASE --work-dir $WORK `
  --message "abandon stuck creating"
```

그다음 UI에서 원하는 환경으로 Ensure를 다시 누르면 새 lease가 발급된다.

### D-3. 결과 확인

**Windows** (이미지가 있는 곳):

```powershell
docker images msbuild-local/profile
Get-ChildItem D:\factory-images
docker run --rm msbuild-local/profile:<tag> cmd /c where msbuild
```

**Linux / 브라우저** (Portal):

- 상태 **READY**  
- digest가 `sha256:simulated-` / `dry-run-`이 **아님**  
- 같은 환경으로 Ensure 다시 → 재사용 (Windows 빌드 재실행 없음)

---

## E. 성공 체크리스트

- [ ] Linux: Portal `/readyz` OK, Windows에서 URL 접속 가능  
- [ ] Windows: `msbuild-agent-base:ltsc2019` 및/또는 `:ltsc2022`  
- [ ] Windows: `vs2019-16.11.54` / `vs2022-17.14.x` 각각에 setup exe  
- [ ] Ensure(연도 일치) → CREATING  
- [ ] Windows build 성공 → 로컬 `msbuild-local/profile:…`  
- [ ] finalize → Portal READY  
- [ ] 컨테이너에서 `msbuild` 확인  

---

## F. 문제 해결

| 증상 | 어느 호스트 | 조치 |
|------|-------------|------|
| Portal 안 뜸 | Linux | `docker compose -f docker-compose.airgap-windows.yml logs` |
| Windows→Portal 연결 실패 | 네트워크 | 방화벽, IP, `:8000` |
| Ensure 후 바로 READY + tar 없음 | Linux | stub compose 쓰는지 확인 → **airgap-windows** 로 교체 |
| CREATING 고정 | Windows | fetch/build/finalize 실행 여부 |
| `unknown flag: --build-context` | Windows | Docker가 구버전. **최신 `portal_factory_agent.py`** 로 교체 후 build 재실행 — 자동으로 layout을 work 디렉터리에 robocopy (수십 GB·시간 소요). 강제: `$env:FACTORY_EMBED_LAYOUT_IN_CONTEXT=1` |
| `lease expired` | 양쪽 | 빌드가 lease TTL(~2h, airgap compose는 **12h**)보다 김. **Portal 이미지/compose 갱신** 후 Ensure(동일 조합)로 lease 연장, 또는 Windows에서 `heartbeat`/`finalize` 재시도(동일 leaseId + 상태가 아직 CREATING이면 soft-renew). reconcile로 FAILED가 됐으면 Ensure → **새 lease**로 finalize (`result.json` 있으면 빌드 재실행 불필요). 최신 agent는 build 중 10분마다 heartbeat |
| `factory slots full` / `FACTORY_BUSY` | Linux UI | 동시 CREATING은 **1개**. 이전 Ensure가 아직 CREATING이면 다른 조합 Ensure가 거절됨. **같은 환경**으로 Ensure → `factoryLeaseId` 받아서 이어서 build/finalize. 포기하려면 Windows에서 `fail`로 슬롯 해제(아래). lease가 이미 만료됐으면 Portal이 Ensure 시 자동 reconcile |
| Ensure 후에도 `leaseExpiresAt`이 과거 · leaseId 동일 | Linux | **구 Portal 이미지**일 가능성 큼. 아래 “만료 lease 즉시 복구” 또는 Portal 이미지 재빌드/재기동. 최신 Portal은 Ensure 시 만료 lease를 **새 leaseId**로 교체하고 TTL을 미래로 민다 |
| `result.json missing` | Windows | build가 실패한 것. build 성공 후에만 finalize |
| `COPY failed: layout` / junction | Windows | 위와 동일 — embed 경로 사용 (에이전트 자동) |
| `FROM` 실패 | Windows | `docker images msbuild-agent-base` |
| vs_setup 없음 | Windows | `IMAGE_FACTORY_LAYOUT_ROOT` |
| HMAC 401 | 양쪽 | 시크릿 문자열 동일 여부 · 시계 동기화 |
| Linux에서 Windows 이미지 없음 | 정상 | 이미지는 **Windows Docker에만** 있음 |

중지 (Linux):

```bash
docker compose -f docker-compose.airgap-windows.yml down
```

---

## 다음에 (아직 아님)

- Nexus push (`FACTORY_SKIP_PUSH=0`)  
- Jenkins `msbuild-factory` 라벨로 agent 자동화  
- k9s  

관련: `docs/factory-host-runbook.md`
