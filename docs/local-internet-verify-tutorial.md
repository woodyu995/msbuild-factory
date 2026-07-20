# 인터넷망 로컬 검증 튜토리얼 (Docker만)

폐쇄망(k9s + Jenkins)에 올리기 **전에**, 인터넷망 PC에서 Portal 동작을 확인하는 순서입니다.

이 환경에서는 **Windows 이미지 실빌드 / Nexus 실push / Jenkins** 는 하지 않습니다.  
대신 Portal이 시나리오대로 **이미지 조회 → (없으면) Factory 시작 상태 → READY → 이미지 정보 반환** 하는지를 **simulation**으로 검증합니다.

---

## 0. 준비물

- Docker Desktop (또는 Docker Engine) + `docker compose`
- **Linux 컨테이너 모드** (중요 — 아래 참고)
- 이 저장소 클론본
- 브라우저
- (선택) `curl`

권장 OS: Linux / macOS / Windows + **WSL2 / Linux containers**

```bash
git clone <repo-url> msbuild-factory
cd msbuild-factory
git checkout cursor/portal-mvp-implementation-03e9   # 작업 브랜치
```

### 0-1. Windows에서 필수: Linux 컨테이너로 전환

Portal Dockerfile은 `node:22-alpine`, `python:3.12-slim` 등 **Linux 이미지**만 사용합니다.  
Docker가 Windows 컨테이너 모드면 아래 에러가 납니다.

```text
no matching manifest for windows(10.0.20348)/amd64 in the manifest list entries
```

해결:

1. **Docker Desktop** 쓰는 경우  
   - 트레이 아이콘 우클릭 → **Switch to Linux containers…**  
   - 전환 후 `docker version` 의 OS/Arch 가 `linux` 인지 확인
2. **Windows Server + Docker** 만 있고 Linux 컨테이너가 불가한 경우  
   - WSL2 / Linux VM에서 실행하거나  
   - 아래 **§5 Docker 없이 호스트 실행** 사용
3. 확인 명령:

```bash
docker info --format '{{.OSType}}'
# 기대값: linux
# windows 가 나오면 아직 Windows 컨테이너 모드입니다.
```

---

## 1. 한 줄로 Portal 띄우기 (추천)

저장소 루트에서:

```bash
# Linux 컨테이너 모드인지 확인한 뒤
docker compose -f docker-compose.local.yml up --build
```

성공하면:

- API/UI: http://127.0.0.1:8000/
- health: http://127.0.0.1:8000/healthz
- ready: http://127.0.0.1:8000/readyz

중지:

```bash
docker compose -f docker-compose.local.yml down
```

> 컨테이너 안에 SQLite를 씁니다. `down` 하면 DB는 사라집니다(검증용이라 문제 없음).

---

## 2. 브라우저로 시나리오 확인 (UI)

1. http://127.0.0.1:8000/ 접속  
2. Hot preset 하나 클릭 (예: **표준 .NET Framework 4.8**)  
3. **1. Ensure image** 클릭  
   - 이미 seed된 Hot이면 곧바로 **READY** + digest 표시  
4. (선택) 프로젝트 정보 입력 후 **2. Start build**  
   - `PORTAL_SIMULATE_WORKERS=true` 이면 자동으로 프로젝트 빌드까지 진행될 수 있음  
5. Cold 조합을 고르면 (예: C++ v143 + MFC, exactReuse)  
   - Ensure 후 잠시 CREATING → simulate가 READY로 만듦  
   - 그다음 Start build 가능  

이것이 시나리오의:

- 빌드 툴 선택  
- 이미지 있는지 검증  
- 있으면 정보 반환 / 없으면 생성(시뮬) 후 정보 반환  

에 해당합니다.

---

## 3. curl로 API만 확인 (선택)

### 3-A. Hot preset — 이미 있는 이미지

```bash
curl -s http://127.0.0.1:8000/api/v1/images/ensure \
  -H 'Content-Type: application/json' \
  -d '{
    "environment": {
      "visualStudio": "2022",
      "dotnetFrameworks": ["4.8"],
      "dotnetSdks": ["8.0"],
      "cppToolsets": [],
      "windowsSdks": [],
      "features": ["managed-desktop"],
      "reuseMode": "preferCompatible"
    }
  }' | python3 -m json.tool
```

기대한 결과:

- `"ready": true`
- `"image": { "digest": "sha256:preset-...", ... }`

### 3-B. Cold — 없으면 생성(시뮬) 후 READY

```bash
# 1) ensure (CREATING 가능)
curl -s http://127.0.0.1:8000/api/v1/images/ensure \
  -H 'Content-Type: application/json' \
  -d '{
    "environment": {
      "visualStudio": "2022",
      "dotnetFrameworks": ["4.8"],
      "dotnetSdks": [],
      "cppToolsets": ["v143"],
      "windowsSdks": ["10.0.22621.0"],
      "features": ["managed-desktop", "mfc"],
      "reuseMode": "exactReuse"
    }
  }' | tee /tmp/ensure.json | python3 -m json.tool

# 2) profile hash 추출
HASH=$(python3 -c "import json;print(json.load(open('/tmp/ensure.json'))['matchedProfileHash'])")
echo "HASH=$HASH"

# 3) READY 될 때까지 폴링 (compose는 auto-simulate ON)
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -s "http://127.0.0.1:8000/api/v1/images/$HASH" | tee /tmp/img.json | python3 -m json.tool
  python3 -c "import json,sys; d=json.load(open('/tmp/img.json')); sys.exit(0 if d.get('ready') else 1)" && break
  sleep 1
done
```

기대한 결과:

- 최종 `"ready": true`
- `"image"."digest"` 가 `sha256:simulated-...` 형태

> auto-simulate가 꺼져 있으면:
> `curl -X POST http://127.0.0.1:8000/api/v1/images/$HASH/simulate`

### 3-C. (선택) 프로젝트 빌드 시작

```bash
DIGEST=$(python3 -c "import json;print(json.load(open('/tmp/img.json'))['image']['digest'])")

curl -s http://127.0.0.1:8000/api/v1/build-requests \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: demo-$(date +%s)" \
  -d "{
    \"project\": {
      \"repository\": \"DemoApp\",
      \"gitRef\": \"main\",
      \"solutionPath\": \"DemoApp.sln\"
    },
    \"environment\": {
      \"visualStudio\": \"2022\",
      \"dotnetFrameworks\": [\"4.8\"],
      \"dotnetSdks\": [],
      \"cppToolsets\": [\"v143\"],
      \"windowsSdks\": [\"10.0.22621.0\"],
      \"features\": [\"managed-desktop\", \"mfc\"],
      \"reuseMode\": \"exactReuse\"
    },
    \"matchedProfileHash\": \"$HASH\",
    \"imageDigest\": \"$DIGEST\"
  }" | python3 -m json.tool
```

기대한 결과: `"status": "BUILD_QUEUED"` 또는 (auto-sim) `"SUCCEEDED"`

READY 전에 빌드하면 `409` + `IMAGE_NOT_READY` 가 나와야 정상입니다.

---

## 4. 자동 스모크 (서버 없이)

Portal 프로세스를 안 띄워도, 저장소만으로 회귀 확인:

```bash
# 의존성
cd portal/backend
python3 -m pip install -r requirements.txt

# 단위/통합 테스트
PYTHONPATH=. python3 -m pytest -q

# cutover 스모크 (ensure → pin → build 포함)
cd ../..
bash portal/scripts/smoke-cutover.sh
```

둘 다 통과하면 API 계약은 인터넷망에서 검증된 것입니다.

---

## 5. (대안) Docker 없이 호스트에서 실행

Docker Compose가 어려울 때만 사용합니다.

터미널 A — Backend:

```bash
cd portal/backend
python3 -m pip install -r requirements.txt
export PORTAL_ALLOW_INSECURE_DEFAULTS=true
export PORTAL_SIMULATE_WORKERS=true
export PORTAL_DEFAULT_ACTOR_ROLES=operator,builder
PYTHONPATH=. python3 -m uvicorn app.main:app --reload --port 8000
```

터미널 B — Frontend (UI 핫리로드):

```bash
cd portal/frontend
npm ci
npm run dev
```

- UI: Vite가 안내하는 주소 (보통 http://127.0.0.1:5173 , `/api` 프록시)
- API: http://127.0.0.1:8000

---

## 6. 이 검증에서 “되는 것 / 안 되는 것”

| 항목 | 인터넷망 로컬 |
|------|----------------|
| 포털 UI / API | ✅ |
| Hot 이미지 재사용(seed) | ✅ |
| Cold ensure → READY(시뮬) | ✅ |
| 이미지 정보(digest) 반환 | ✅ |
| build-requests 핀 검증 | ✅ |
| 실제 Windows Docker build | ❌ |
| Nexus 실 push/pull | ❌ |
| 폐쇄망 Jenkins 트리거 | ❌ (여기선 생략) |

실 Factory/Nexus/Jenkins는 **폐쇄망 k9s 배포 후** 이어서 검증합니다.

---

## 7. 문제 생기면

| 증상 | 확인 |
|------|------|
| `no matching manifest for windows(10.0.20348)/amd64` | Docker가 Windows 컨테이너 모드임 → **Linux containers로 전환** (§0-1). Portal은 Linux 전용. |
| 8000 포트 충돌 | `docker compose ... down` 후 재실행, 또는 다른 포트 매핑 |
| UI는 뜨는데 API 실패 | `/healthz`, `/readyz` 확인 |
| Ensure 후 영원히 CREATING | compose의 `PORTAL_SIMULATE_WORKERS=true` 인지 확인, 또는 `/images/{hash}/simulate` 수동 호출 |
| Start build 409 | Ensure가 READY인지, 환경 바꾼 뒤 핀이 남아있지 않은지(UI는 환경 변경 시 핀 초기화) |
| `npm ci` / build 실패 | Node 22+, lockfile 포함 여부 |

로그:

```bash
docker compose -f docker-compose.local.yml logs -f portal
```

---

## 8. 다음 단계 (폐쇄망 이동 미리보기)

인터넷망 검증이 끝나면 USB로 옮길 후보:

1. `docker save` 한 `msbuild-portal` 이미지 (및 postgres 등)
2. 이 git 저장소 (또는 릴리즈 tar)
3. `k8s/portal/*`, `k8s/postgres/*` 매니페스트 + 편집한 secret

폐쇄망에서는 기존 Jenkins에 credentials/Job만 연결하고 Portal Deployment를 올리면 됩니다.  
자세한 연동은 `jenkins/WIRING.md`, `k8s/portal/README.md` 참고.
