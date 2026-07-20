# 폐쇄망 Docker 검증 튜토리얼  
## 포털 환경 설정 → 이미지 생성(Nexus)까지 (K8s 불필요)

목표: 폐쇄망에서 **Kubernetes 없이** Docker로 Portal을 띄우고,  
사용자 포털에서 빌드 환경을 고른 뒤 **실제(또는 dry-run) 이미지 생성**까지 확인한다.

```text
[사용자 브라우저]
    → [Portal 컨테이너] ensure
        → [기존 Jenkins] msbuild-image-factory
            → [Windows Factory 호스트] docker build → Nexus push
        → Portal READY + digest
```

프로젝트 MSBuild(Windows Pod)는 이 문서 범위 밖입니다. **이미지 확보까지**에 집중합니다.

---

## 0. 필요한 것 (폐쇄망)

| 구성 | 역할 | K8s? |
|------|------|------|
| Linux Docker 호스트 | Portal + Postgres 컨테이너 | 불필요 |
| Jenkins (기존) | Factory job 트리거 | 불필요 |
| Windows Factory 호스트 | `msbuild-factory` 에이전트, Windows Docker | 불필요 (전용 VM/호스트) |
| Nexus Docker registry | 이미지 push/pull | 불필요 |

네트워크:

- Jenkins → Portal URL 접근 가능  
- Factory 호스트 → Portal, Nexus 접근 가능  
- 사용자 PC → Portal `:8000` (또는 리버스 프록시) 접근 가능  

---

## 1. 인터넷망에서 USB 반입물 만들기 (Intel Mac)

```bash
cd msbuild-factory
git checkout cursor/portal-mvp-implementation-03e9
git pull

# Portal 이미지 빌드 후 태그
docker compose -f docker-compose.local.yml build
docker tag msbuild-factory-portal:latest msbuild-portal:airgap

# 반출 tar
docker pull postgres:16-alpine
docker save msbuild-portal:airgap -o msbuild-portal.tar
docker save postgres:16-alpine -o postgres-16-alpine.tar

# 소스/설정도 함께
tar czf msbuild-factory-src.tgz \
  --exclude .git \
  .
```

USB에 넣을 목록:

- `msbuild-portal.tar`
- `postgres-16-alpine.tar`
- `msbuild-factory-src.tgz` (또는 git bundle)
- (이미 없다면) Windows `agent-base` / VS offline layout — Factory 실빌드용

---

## 2. 폐쇄망 Linux 호스트 — Portal 기동

```bash
# USB에서 로드
docker load -i msbuild-portal.tar
docker load -i postgres-16-alpine.tar
tar xzf msbuild-factory-src.tgz
cd msbuild-factory   # 압축 푼 루트

cp .env.airgap.example .env.airgap
# .env.airgap 편집 (아래 표 참고)

docker compose -f docker-compose.airgap.yml --env-file .env.airgap up -d
docker compose -f docker-compose.airgap.yml --env-file .env.airgap ps
curl -s http://127.0.0.1:8000/readyz
```

### `.env.airgap` 핵심 값

| 변수 | 예시 | 설명 |
|------|------|------|
| `PORTAL_CALLBACK_HMAC_SECRET` | 긴 랜덤 | Jenkins `portal-callback-hmac`과 **동일** |
| `PORTAL_API_TOKENS` | `portal-ui:…:builder,jenkins-bot:…:operator\|builder` | UI/봇 토큰 |
| `PORTAL_JENKINS_URL` | `http://jenkins.internal:8080/` | 기존 Jenkins |
| `PORTAL_JENKINS_USERNAME` / `_API_TOKEN` | portal-bot | Portal→Jenkins 트리거 |
| `PORTAL_REGISTRY_HOST` | `nexus.internal:8082` | **Factory Windows가 해석 가능한** Nexus |
| `POSTGRES_PASSWORD` | … | DB |
| `PORTAL_SIMULATE_WORKERS` | compose에 `false` 고정 | 실 Jenkins 경로 |

브라우저: `http://<linux-host>:8000/`  
로그인: API token을 UI의 Bearer 칸에 입력 (`portal-ui` 토큰).

---

## 3. Jenkins 쪽 최소 연결 (이미지 생성용)

이미 Jenkins가 있으므로 Job/Credential만 추가합니다. 상세는 `jenkins/WIRING.md`.

### Credentials
| ID | 값 |
|----|-----|
| `portal-base-url` | `http://<linux-host>:8000` (Factory/Jenkins가 닿는 주소) |
| `portal-callback-hmac` | `.env.airgap`의 HMAC과 동일 |
| `nexus-docker` | Nexus docker username/password |
| `portal-jenkins-api` | Portal이 쓰는 Jenkins API 사용자 (Portal env와 일치) |

### Node
- Windows 호스트를 에이전트로 등록  
- Label: **`msbuild-factory`**  
- Windows containers 모드 Docker 사용 가능해야 함  

### Job
- Pipeline: `jenkins/jobs/msbuild-image-factory/Jenkinsfile`  
- Agent: `msbuild-factory`  

저장소 스크립트가 에이전트에서 보이도록 함 (checkout SCM 또는 공유 경로).

---

## 4. Windows Factory 호스트 준비

`docs/factory-host-runbook.md` 요약:

1. Windows Server + Docker (Windows containers)  
2. 디스크 여유 충분 (실빌드 시 수백 GB 권장)  
3. Offline layout / installer 경로  
4. Nexus `docker login` 가능 여부 확인  

### 단계적 검증 권장

| 단계 | 설정 | 확인 내용 |
|------|------|-----------|
| A. Dry-run | `FACTORY_DRY_RUN=1` (또는 agent `--dry-run`) | Portal↔Jenkins↔agent 콜백, READY(가짜 digest) |
| B. 실빌드 | `FACTORY_DRY_RUN=0` | 실제 `docker build` + Nexus push + 실 digest |

처음에는 **A**로 포털→Factory 연동만 확인한 뒤 **B**로 가는 것을 권장합니다.

---

## 5. 포털에서 이미지 생성 테스트

### 5-1. Hot (이미 있으면 재사용)

1. UI 접속 → Bearer 토큰 입력  
2. Preset **표준 .NET Framework 4.8** 등 선택  
3. **Ensure image**  
4. 기대: 즉시 **READY** + digest (seed 또는 이전 생성분)

### 5-2. Cold (없으면 Factory로 생성)

1. 환경을 cold로 구성 예:  
   - VS 2022  
   - net48 + C++ v143 + WinSDK + MFC  
   - reuseMode = `exactReuse`  
2. **Ensure image**  
3. 기대 UI: `CREATING`  
4. Jenkins에서 `msbuild-image-factory` 실행 확인  
5. Factory 로그: fetch → build → (push) → finalize  
6. UI 폴링 후 **READY** + digest  

확인 API:

```bash
# Linux 호스트에서
TOKEN='portal-ui용-토큰'

curl -s http://127.0.0.1:8000/api/v1/images/ensure \
  -H "Authorization: Bearer $TOKEN" \
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
  }' | python3 -m json.tool
```

`matchedProfileHash`로 상태 확인:

```bash
HASH=<위 응답의 matchedProfileHash>
curl -s http://127.0.0.1:8000/api/v1/images/$HASH \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

실빌드 성공 시:

- `"ready": true`  
- digest가 `sha256:simulated-` / `sha256:dry-run-` 가 **아닌** Nexus 실 digest  
- Nexus UI/CLI에서 해당 repo에 태그 존재  

같은 환경으로 Ensure를 다시 하면 Factory가 **다시 안 돌고** READY 재사용되어야 합니다.

---

## 6. 성공 체크리스트

- [ ] `docker compose ... up -d` 후 `/readyz` OK  
- [ ] UI 로그인(Bearer) 가능  
- [ ] Hot Ensure → READY  
- [ ] Cold Ensure → Jenkins Factory job 기동  
- [ ] (Dry-run) finalize → Portal READY  
- [ ] (실빌드) Nexus에 이미지 push됨 + Portal READY + 실 digest  
- [ ] 동일 환경 재 Ensure → Factory 미실행(재사용)

여기까지면 **「환경 설정 → 이미지 생성」** Docker 기반 검증 완료입니다.

---

## 7. 문제 해결

| 증상 | 원인 / 조치 |
|------|-------------|
| Ensure 후 CREATING 고정 | Jenkins URL/토큰 오류, job 미생성, agent offline |
| Factory job은 도는데 Portal 401/HMAC | `portal-callback-hmac` ≠ Portal HMAC |
| docker push 실패 | `PORTAL_REGISTRY_HOST`가 Factory에서 안 열림, `nexus-docker`  cred 오류 |
| VS 설치 실패 / 디스크 | layout 경로, 여유 공간(실빌드는 매우 큼) |
| UI 401 | Bearer 토큰 / `PORTAL_API_TOKENS` 확인 |
| simulate로만 READY | compose에 `PORTAL_SIMULATE_WORKERS=false` 인지 확인 (airgap 파일은 false 고정) |

로그:

```bash
docker compose -f docker-compose.airgap.yml --env-file .env.airgap logs -f portal
```

---

## 8. 이 튜토리얼 범위 밖 (다음에)

- Windows K8s Pod에서 솔루션 MSBuild (`Start build` 실경로)  
- k9s Deployment로 Portal 이전  

이미지 생성만 검증한 뒤, 여유가 되면 project-build Job + Windows 워커를 연결하면 됩니다.

관련 문서:

- Factory 호스트: `docs/factory-host-runbook.md`  
- Jenkins: `jenkins/WIRING.md`  
- 인터넷망 Mac 사전 검증: `docs/local-internet-verify-tutorial.md`
