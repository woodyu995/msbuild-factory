# 인터넷망 로컬 검증 튜토리얼 (Intel macOS + Docker)

대상: **Intel 기반 macOS** (예: MacBook Pro 2019 등), Docker만 사용 가능한 인터넷망 PC.

폐쇄망(k9s + Jenkins + Windows Factory)에 올리기 **전에**, 이 Mac에서 Portal이 아래 시나리오대로 동작하는지 확인합니다.

1. 포털 접속  
2. 빌드 툴 선택  
3. 이미지 있으면 정보 반환  
4. 없으면 생성(로컬 **simulation**) → READY → 이미지 정보 반환  

이 단계에서는 **실제 Windows Docker 빌드 / Nexus push / Jenkins 호출**은 하지 않습니다.

---

## 0. 준비물 (Intel Mac)

| 항목 | 권장 |
|------|------|
| OS | macOS (Intel / x86_64) |
| Docker | [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) |
| Compose | Docker Desktop에 포함 (`docker compose`) |
| 기타 | 브라우저, Terminal.app 또는 iTerm |

칩 확인:

```bash
uname -m
# 기대값: x86_64   ← Intel Mac
# arm64 이면 Apple Silicon용 안내가 따로 필요합니다 (이 문서는 Intel 기준)
```

Docker 확인:

```bash
docker version
docker compose version
docker info --format '{{.OSType}} / {{.Architecture}}'
# 기대 예: linux / x86_64
```

저장소:

```bash
git clone <repo-url> msbuild-factory
cd msbuild-factory
git checkout cursor/portal-mvp-implementation-03e9
git pull
```

> Intel Mac + Docker Desktop은 기본적으로 **Linux 컨테이너**를 씁니다.  
> Windows의 “Switch to Linux containers” 이슈는 Mac에서는 해당 없습니다.

---

## 1. Portal 한 번에 실행

저장소 **루트**에서:

```bash
cd ~/path/to/msbuild-factory

docker compose -f docker-compose.local.yml up --build
```

첫 빌드는 Node/Python 레이어 때문에 수 분 걸릴 수 있습니다. 로그에 `Uvicorn running on http://0.0.0.0:8000` 이 보이면 준비된 것입니다.

브라우저에서:

| URL | 용도 |
|-----|------|
| http://127.0.0.1:8000/ | 포털 UI |
| http://127.0.0.1:8000/healthz | 상태 |
| http://127.0.0.1:8000/readyz | Ready |

중지(다른 터미널 또는 `Ctrl+C` 후):

```bash
docker compose -f docker-compose.local.yml down
```

> 컨테이너 내부 SQLite를 씁니다. `down` 하면 DB는 초기화됩니다(검증용 OK).

백그라운드 실행을 원하면:

```bash
docker compose -f docker-compose.local.yml up --build -d
docker compose -f docker-compose.local.yml logs -f portal
```

---

## 2. UI로 시나리오 검증 (필수)

1. http://127.0.0.1:8000/ 접속  
2. Hot preset 클릭 — 예: **표준 .NET Framework 4.8** 또는 **표준 .NET 8 Windows**  
3. **1. Ensure image** 클릭  
   - Hot은 seed 이미지라 곧바로 **READY** + `digest` 가 보여야 함  
4. (선택) 프로젝트 칸을 채운 뒤 **2. Start build**  
   - `PORTAL_SIMULATE_WORKERS=true` 이라 빌드 상태가 자동으로 끝날 수 있음  
5. Cold 조합 검증  
   - Reuse mode를 `exactReuse`로 두고 C++ / MFC 등을 골라 Ensure  
   - 잠시 `CREATING` 후 **READY** (로컬 simulate)  
   - 그다음 Start build 가능  

통과 기준:

- Hot: Ensure 즉시 READY + digest  
- Cold: Ensure 후 READY + digest 반환  
- READY 전 Start build 시 오류(이미지 미준비)가 나는 것도 정상  

---

## 3. 터미널로 API 확인 (선택)

Mac 기본 `curl` + `python3` 사용.

### 3-A. Hot — 이미지 있음

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

기대: `"ready": true`, `"image"."digest"` 존재.

### 3-B. Cold — 없으면 생성(시뮬) 후 READY

```bash
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

HASH=$(python3 -c "import json; print(json.load(open('/tmp/ensure.json'))['matchedProfileHash'])")
echo "HASH=$HASH"

for i in $(seq 1 15); do
  curl -s "http://127.0.0.1:8000/api/v1/images/$HASH" | tee /tmp/img.json | python3 -m json.tool
  python3 -c "import json,sys; d=json.load(open('/tmp/img.json')); sys.exit(0 if d.get('ready') else 1)" && break
  sleep 1
done
```

기대: 최종 `"ready": true`, digest가 `sha256:simulated-...` 형태.

### 3-C. (선택) 프로젝트 빌드 요청

```bash
DIGEST=$(python3 -c "import json; print(json.load(open('/tmp/img.json'))['image']['digest'])")

curl -s http://127.0.0.1:8000/api/v1/build-requests \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: mac-demo-$(date +%s)" \
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

기대: `"status": "BUILD_QUEUED"` 또는 곧 `"SUCCEEDED"` (auto-sim).

---

## 4. 자동 테스트 (서버 없이)

Portal 컨테이너를 안 띄워도, Mac에서 회귀 확인 가능합니다.

```bash
cd portal/backend
python3 -m pip install -r requirements.txt
PYTHONPATH=. python3 -m pytest -q

cd ../..
bash portal/scripts/smoke-cutover.sh
```

둘 다 통과하면 API 계약은 검증된 상태입니다.

> `python3` / `pip` 가 없으면:  
> `xcode-select --install` 후, 필요 시 `brew install python`  
> 또는 이 절은 건너뛰고 §1–2 UI 검증만 해도 됩니다.

---

## 5. (대안) Docker 없이 Mac 호스트 실행

Docker 빌드가 느릴 때만 사용합니다. Terminal **두 개**가 필요합니다.

**터미널 A — API**

```bash
cd portal/backend
python3 -m pip install -r requirements.txt
export PORTAL_ALLOW_INSECURE_DEFAULTS=true
export PORTAL_SIMULATE_WORKERS=true
export PORTAL_DEFAULT_ACTOR_ROLES=operator,builder
export PYTHONPATH=.
python3 -m uvicorn app.main:app --reload --port 8000
```

**터미널 B — UI**

```bash
cd portal/frontend
npm ci
npm run dev
```

- UI: 터미널에 표시된 주소 (보통 http://127.0.0.1:5173 )  
- API: http://127.0.0.1:8000  

Node가 없으면: `brew install node` (Node 22 권장).

---

## 6. 이 Mac 검증에서 되는 것 / 안 되는 것

| 항목 | Intel Mac 로컬 |
|------|----------------|
| 포털 UI / API | ✅ |
| Hot 이미지 재사용 (seed) | ✅ |
| Cold ensure → READY (simulation) | ✅ |
| 이미지 digest 반환 | ✅ |
| build-requests 핀 검증 | ✅ |
| 실제 Windows Server Core 이미지 빌드 | ❌ (폐쇄망 Factory) |
| Nexus 실 push/pull | ❌ (폐쇄망) |
| 폐쇄망 Jenkins 연동 | ❌ (폐쇄망에서 연결) |

---

## 7. Intel Mac 흔한 문제

| 증상 | 조치 |
|------|------|
| `Cannot connect to the Docker daemon` | Docker Desktop 실행, 고래 아이콘이  Idle/Running 인지 확인 |
| 빌드 중 `npm ci` / 네트워크 실패 | 사내 프록시면 Docker Desktop → Settings → Resources / Proxies 설정 |
| `port is already allocated` (8000) | `lsof -i :8000` 후 기존 프로세스 종료, 또는 compose 포트 변경 |
| Ensure 후 계속 CREATING | compose에 `PORTAL_SIMULATE_WORKERS=true` 인지 확인. 수동: `curl -X POST http://127.0.0.1:8000/api/v1/images/<hash>/simulate` |
| Start build 409 | Ensure가 READY인지 확인. UI에서 환경 바꾸면 핀이 초기화됨 → Ensure 다시 |
| Apple Silicon(M1/M2)에서 이 문서를 연 경우 | `uname -m` 이 `arm64` → 별도 확인 필요. Intel(`x86_64`)만 이 문서 대상 |

로그:

```bash
docker compose -f docker-compose.local.yml logs -f portal
```

---

## 8. 검증 체크리스트 (복사해서 사용)

- [ ] `uname -m` → `x86_64`
- [ ] `docker compose -f docker-compose.local.yml up --build` 성공
- [ ] http://127.0.0.1:8000/healthz → `"status":"ok"` (또는 database true)
- [ ] Hot Ensure → READY + digest
- [ ] Cold Ensure → READY + digest (simulated)
- [ ] (선택) `pytest` / `smoke-cutover.sh` 통과

모두 체크되면 인터넷망 Portal 검증 완료 → USB로 폐쇄망 반입 준비로 넘어가면 됩니다.

---

## 9. 다음 단계 (폐쇄망 미리보기)

Mac에서 검증이 끝나면 USB로 옮길 후보:

1. Portal 이미지:  
   `docker compose -f docker-compose.local.yml build` 후  
   `docker save msbuild-factory-portal:latest -o msbuild-portal.tar`  
   (이미지 이름은 `docker images` 로 확인)
2. git 저장소 또는 릴리즈 tar  
3. `k8s/portal/*`, `k8s/postgres/*` + 편집한 secret  

폐쇄망에서는 기존 Jenkins에 credentials/Job 연결 + Portal/Postgres apply.  
연동 상세: `jenkins/WIRING.md`, `k8s/portal/README.md`.
