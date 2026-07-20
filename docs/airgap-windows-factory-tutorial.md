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

## A. 인터넷망에서 USB 만들기 (한 번)

Linux 또는 Mac(Docker)에서 Portal 이미지:

```bash
git checkout cursor/portal-mvp-implementation-03e9
git pull

docker compose -f docker-compose.local.yml build
docker tag msbuild-factory-portal:latest msbuild-portal:airgap
docker save msbuild-portal:airgap -o msbuild-portal.tar
tar czf msbuild-factory-src.tgz --exclude .git --exclude local-images .
```

Windows(인터넷, Windows containers)에서 base:

```powershell
docker pull mcr.microsoft.com/windows/servercore:ltsc2022
docker save mcr.microsoft.com/windows/servercore:ltsc2022 -o servercore-ltsc2022.tar
```

VS Build Tools **offline layout**도 인터넷망에서 만들어 둔다  
(Catalog `layoutRelease` 예: `vs2022-17.14.x`, 폴더 안에 `vs_setup.exe`).

### USB에 넣을 목록

| 파일/폴더 | 넣는 호스트 |
|-----------|-------------|
| `msbuild-portal.tar` | → Linux |
| `msbuild-factory-src.tgz` | → Linux **와** Windows 둘 다 |
| `servercore-ltsc2022.tar` | → Windows |
| VS offline layout 폴더 | → Windows |
| (선택) .NET SDK `.exe` | → Windows `installers\` |

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
