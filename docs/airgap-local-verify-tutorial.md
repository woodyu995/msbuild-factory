# 폐쇄망 검증 튜토리얼 (이미지 빌드까지)

목표: 폐쇄망 Linux Docker 호스트에서 **로그인 / Jenkins / Nexus 없이**  
포털에서 환경을 고르면 **이미지가 실제로 빌드·저장**되는 것까지 확인한다.

```text
[인터넷망] Portal 이미지 빌드 → USB
                ↓
[폐쇄망] docker load → compose up
                ↓
브라우저 → Ensure → 호스트 Docker로 build + save → READY
                ↓
        ./local-images/<tag>.tar
        docker images msbuild-local
```

필요: **Linux Docker가 돌아가는 폐쇄망 호스트** 1대 (K8s/Jenkins/Nexus 불필요).

> 이 단계 이미지는 검증용 stub (`FROM scratch` + 프로필 메타데이터)입니다.  
> **실제 Server Core + VS Build Tools** 는  
> **[airgap-windows-factory-tutorial.md](./airgap-windows-factory-tutorial.md)** 를 사용하세요.

---

## A. 인터넷망에서 USB 만들기

Intel Mac / Linux 등 Docker가 되는 PC:

```bash
git clone <repo-url> msbuild-factory
cd msbuild-factory
git checkout cursor/portal-mvp-implementation-03e9
git pull

# Portal 이미지 빌드 (인터넷 필요: node/python base + docker CLI)
docker compose -f docker-compose.local.yml build
docker tag msbuild-factory-portal:latest msbuild-portal:airgap

# 반출
docker save msbuild-portal:airgap -o msbuild-portal.tar

# 폐쇄망에서 compose/문서를 쓰기 위한 소스 (또는 필요한 파일만)
tar czf msbuild-factory-src.tgz \
  --exclude .git \
  --exclude local-images \
  --exclude '**/__pycache__' \
  --exclude '**/node_modules' \
  .
```

USB에 넣을 것:

| 파일 | 설명 |
|------|------|
| `msbuild-portal.tar` | Portal 컨테이너 이미지 |
| `msbuild-factory-src.tgz` | compose + 문서 (최소 `docker-compose.airgap-local.yml`만 있어도 됨) |

---

## B. 폐쇄망에서 기동

```bash
# USB에서
docker load -i msbuild-portal.tar
tar xzf msbuild-factory-src.tgz
cd msbuild-factory   # 압축 푼 루트

mkdir -p local-images
docker compose -f docker-compose.airgap-local.yml up -d
docker compose -f docker-compose.airgap-local.yml ps
curl -s http://127.0.0.1:8000/readyz
```

브라우저: `http://<폐쇄망-호스트>:8000/`

- 로그인 없음  
- API 토큰 없음  
- Ensure image만 사용  

---

## C. 이미지 빌드 확인 (여기가 핵심)

1. Reuse mode = **exactReuse**  
2. Hot에 없는 조합 선택 (예: VS 2022 + net48 + **C++ v143** + WinSDK + **MFC**)  
3. **Ensure image**  
4. 상태: `CREATING` → `READY`  
5. 화면에 표시:
   - digest (`sha256:…` — simulated/dry-run 아님)
   - local image: `msbuild-local:vs2022-…`
   - saved tar: `./local-images/<tag>.tar`

호스트 터미널:

```bash
ls -la ./local-images/
docker images msbuild-local

# tar 재확인
docker load -i ./local-images/<tag>.tar
docker image inspect msbuild-local:<tag> --format '{{json .Config.Labels}}'
```

같은 환경으로 Ensure를 다시 누르면 **재사용** (다시 빌드 안 함).

---

## D. 성공 체크리스트

- [ ] USB load 후 `/readyz` OK  
- [ ] 포털 접속, 로그인 없음  
- [ ] Cold Ensure → CREATING → READY  
- [ ] `./local-images/*.tar` 생김  
- [ ] `docker images msbuild-local`에 태그 보임  
- [ ] Labels에 `company.build.mode=local-verify`  
- [ ] 재 Ensure → READY 재사용  

여기까지면 **폐쇄망에서 「환경 선택 → 이미지 빌드」** 검증 완료입니다.

---

## E. 문제 해결

| 증상 | 조치 |
|------|------|
| `msbuild-portal:airgap` 없음 | `docker load -i msbuild-portal.tar` 다시 |
| Ensure 후 FAILED | `docker compose -f docker-compose.airgap-local.yml logs -f portal` — `docker.sock` 마운트 확인 |
| docker.sock 권한 | 호스트 Docker가 root/소켓 접근 가능한지 |
| Hot만 READY, tar 없음 | Hot은 시드. Cold + `exactReuse` 사용 |
| 빌드가 pull 하려 함 | stub는 `FROM scratch`라 pull 불필요. Portal 이미지 자체가 오래됐으면 USB 이미지 재생성 |

중지:

```bash
docker compose -f docker-compose.airgap-local.yml down
```

---

## 다음에 (아직 아님)

- Jenkins + Windows Factory 실빌드  
- Nexus push  
- 로그인 / 크리덴셜  
- k9s 배포  

그 단계는 `docs/airgap-docker-image-factory-tutorial.md` 참고.
