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

예상 USB 용량: Portal 수 GB + Server Core ~몇 GB + VS layout **수십 GB** → USB/외장 HDD **100GB+** 권장.

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

```powershell
mkdir D:\airgap-export -Force
cd D:\airgap-export

# Windows containers 모드에서
docker pull mcr.microsoft.com/windows/servercore:ltsc2022
docker images mcr.microsoft.com/windows/servercore:ltsc2022

docker save mcr.microsoft.com/windows/servercore:ltsc2022 -o D:\airgap-export\servercore-ltsc2022.tar
Get-Item D:\airgap-export\servercore-ltsc2022.tar | Format-List Name, Length
```

- VS 2019 프로필을 쓰려면 **ltsc2019**도 같은 방식으로 추가 반입한다.  
- 폐쇄망에서는 이걸 `msbuild-agent-base:ltsc2022`로 retag 한다 (B/C 단계).

---

### A-5. VS Build Tools offline layout 만들기 (Windows, 인터넷)

Catalog의 `layoutRelease`와 맞춘다. 현재 VS 2022 예: **`vs2022-17.14.x`**.

1. [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) 부트스트랩퍼 다운로드  
   (`vs_BuildTools.exe` 또는 `vs_setup.exe`로 저장)
2. 포함할 workload는 포털에서 고를 조합을 덮어야 한다. 최소 예(Managed + C++ + MFC/ATL + WinSDK):

```powershell
mkdir D:\airgap-export\vs2022-17.14.x -Force
cd D:\airgap-export

# 부트스트랩퍼 이름을 vs_setup.exe 로 맞춰 두면 폐쇄망 스크립트와 동일
# (다운로드 파일명이 vs_BuildTools.exe 이면 rename)

.\vs_BuildTools.exe `
  --layout D:\airgap-export\vs2022-17.14.x `
  --lang en-US `
  --add Microsoft.VisualStudio.Workload.MSBuildTools `
  --add Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools `
  --add Microsoft.VisualStudio.Workload.VCTools `
  --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
  --add Microsoft.VisualStudio.Component.VC.MFC `
  --add Microsoft.VisualStudio.Component.VC.ATL `
  --add Microsoft.VisualStudio.Component.Windows11SDK.22621 `
  --includeRecommended
```

완료 후 확인:

```powershell
Get-ChildItem D:\airgap-export\vs2022-17.14.x\vs_setup.exe, `
              D:\airgap-export\vs2022-17.14.x\vs_BuildTools.exe `
              -ErrorAction SilentlyContinue

# 레이아웃 루트에 setup이 있어야 함 (하위만 있으면 폴더 구조를 맞춤)
(Get-ChildItem D:\airgap-export\vs2022-17.14.x -Recurse -Filter "vs_*.exe" |
  Select-Object -First 5 FullName)
```

폐쇄망에서 `IMAGE_FACTORY_LAYOUT_ROOT`는 **setup exe가 있는 폴더**를 가리킨다.

- 용량 큼 (수십 GB). 레이아웃 생성이 끊기면 같은 명령으로 재개되는 경우가 많다.  
- workload를 빼먹으면 폐쇄망 빌드 시 MSBuild/MFC validation에서 실패한다.

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
  00-README.txt                 # 브랜치명, 날짜, linux-ip 메모용
  linux\
    msbuild-portal.tar
    msbuild-factory-src.tgz
  windows\
    servercore-ltsc2022.tar
    msbuild-factory-src.tgz     # Linux와 동일한 소스 (agent용)
    vs-layouts\
      vs2022-17.14.x\           # vs_setup.exe 포함
    installers\                 # 비어 있어도 OK (폴더는 유지)
```

`00-README.txt` 예:

```text
branch: cursor/portal-mvp-implementation-03e9
portal tag: msbuild-portal:airgap
hmac (폐쇄망 compose 기본): airgap-dev-hmac-change-me
base: servercore ltsc2022 → retag msbuild-agent-base:ltsc2022
layout: vs2022-17.14.x
```

### USB에 넣을 목록 (요약)

| 파일/폴더 | 용량 감 | 폐쇄망에서 푸는 곳 |
|-----------|---------|-------------------|
| `msbuild-portal.tar` | 중 | **Linux** `docker load` |
| `msbuild-factory-src.tgz` | 소 | **Linux + Windows** |
| `servercore-ltsc2022.tar` | 중 | **Windows** `docker load` |
| `vs2022-17.14.x\` layout | **대** | **Windows** 디스크 |
| `installers\` | 소~중 | **Windows** |

복사 후 USB에서 `Get-FileHash` / `ls -lh`로 깨짐 여부를 한 번 확인하는 것을 권장한다.

---

## B. 폐쇄망 Linux 호스트 — Portal


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
3. base load + Catalog가 기대하는 이름으로 tag  
4. layout / installers 경로 준비  
5. Python 3 설치

```powershell
docker load -i servercore-ltsc2022.tar
docker tag mcr.microsoft.com/windows/servercore:ltsc2022 msbuild-agent-base:ltsc2022
docker images msbuild-agent-base

# 레이아웃 예 (vs_setup.exe 가 이 폴더 안에 있어야 함)
# D:\vs-layouts\vs2022-17.14.x\vs_setup.exe
# D:\installers\   (비어 있어도 폴더는 필요)

tar xzf msbuild-factory-src.tgz -C C:\
cd C:\msbuild-factory   # 실제 푼 경로에 맞게
```

환경 변수 (세션마다):

```powershell
$env:PORTAL_URL = "http://10.0.0.10:8000"          # Linux Portal
$env:PORTAL_HMAC_SECRET = "airgap-dev-hmac-change-me"
$env:IMAGE_FACTORY_LAYOUT_ROOT = "D:\vs-layouts\vs2022-17.14.x"
$env:IMAGE_FACTORY_INSTALLER_ROOT = "D:\installers"
$env:FACTORY_DRY_RUN = "0"
$env:FACTORY_SKIP_PUSH = "1"                       # Nexus 아직 안 씀
$env:FACTORY_DOCKER_SAVE_DIR = "D:\factory-images"
$env:IMAGE_FACTORY_STRICT = "1"
```

---

## D. 검증 시나리오 (양쪽 협력)

### D-1. Linux Portal UI에서 Ensure

1. `http://<linux-ip>:8000/` 접속  
2. Reuse mode = **exactReuse**  
3. Cold 조합 예: VS 2022 + net48 + C++ v143 + WinSDK + MFC  
4. **Ensure image**  
5. 상태가 **CREATING**인지 확인 (여기서 멈추는 것이 정상 — Windows가 빌드해야 함)

UI에 나온 `profile` / hash와, 필요하면 Linux에서 lease 확인:

```bash
curl -s http://127.0.0.1:8000/api/v1/images/<HASH>
# factoryLeaseId 사용
```

### D-2. Windows에서 실빌드

```powershell
cd C:\msbuild-factory
$HASH = "<UI의 matchedProfileHash>"
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

- 빌드: VS 설치 포함 **수십 분 ~ 수 시간**  
- 디스크: **수백 GB** 여유 권장  
- Windows Docker가 `msbuild-agent-base:ltsc2022` 위에서 `vs_setup --noWeb` 실행

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
- [ ] Windows: `msbuild-agent-base:ltsc2022` 존재  
- [ ] Windows: layout에 `vs_setup.exe` 존재  
- [ ] Ensure → CREATING  
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
| `FROM` 실패 | Windows | `docker images msbuild-agent-base` |
| vs_setup 없음 | Windows | `IMAGE_FACTORY_LAYOUT_ROOT` |
| HMAC 401 | 양쪽 | 시크릿 문자열 동일 여부 |
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
