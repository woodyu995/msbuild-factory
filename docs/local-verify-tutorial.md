# 로컬 단계 검증 튜토리얼 (단순)

목표: **로그인 / Jenkins / Nexus 없이** 웹 포털에서 환경을 고르면  
그에 맞는 이미지가 **로컬에 빌드·저장**되고, 결과(digest / tar)만 확인한다.

```text
브라우저 → Portal → Ensure → 로컬 docker build + docker save → READY
                 ↓
         ./local-images/<tag>.tar
         docker images msbuild-local
```

> 이 단계의 이미지는 **검증용 stub**입니다 (`FROM scratch` + 프로필 메타데이터).  
> 실제 Windows Server Core MSBuild 이미지 + Nexus push는 이후 단계에서 연결합니다.

---

## 1. 실행

저장소 루트:

```bash
git checkout cursor/portal-mvp-implementation-03e9
docker compose -f docker-compose.local.yml up --build
```

브라우저: **http://127.0.0.1:8000/**

- 로그인 없음  
- API 토큰 입력란 없음  
- 프로젝트 빌드 버튼 없음  

---

## 2. 포털에서 할 일

1. Preset을 고르거나 VS / Targeting Pack / C++ 등을 선택  
2. Cold 경로를 보려면 **Reuse mode = exactReuse** + Hot에 없는 조합(예: C++ v143 + MFC)  
3. **Ensure image** 클릭  
4. 잠시 후 결과가 **READY**  
5. 화면에 표시되는 것:
   - `digest`
   - `local image` (예: `msbuild-local:vs2022-…`)
   - `saved tar` → `./local-images/<tag>.tar`

Hot Preset(이미 시드된 READY)을 고르면 **빌드 없이 즉시 READY**가 납니다.  
“새로 빌드되는지”를 보려면 Cold + `exactReuse`를 쓰세요.

---

## 3. 로컬에서 이미지 확인

```bash
# 호스트에서
ls -la ./local-images/

docker images msbuild-local

# tar로도 확인 가능
docker load -i ./local-images/<tag>.tar
docker image inspect msbuild-local:<tag> --format '{{json .Config.Labels}}'
```

같은 환경으로 Ensure를 다시 누르면 Factory가 **다시 돌지 않고** READY를 재사용합니다.

---

## 4. 성공 기준

- [ ] `http://127.0.0.1:8000/` 접속, 로그인 없음  
- [ ] Cold Ensure → CREATING → READY  
- [ ] `./local-images/*.tar` 생성됨  
- [ ] `docker images msbuild-local`에 태그 보임  
- [ ] 재 Ensure → 재사용  

---

## 5. 문제 해결

| 증상 | 조치 |
|------|------|
| Ensure 후 FAILED | `docker compose … logs portal` — Docker socket 마운트/`docker` CLI 확인 |
| 권한 오류 (docker.sock) | Docker Desktop / 호스트에서 소켓 접근 가능 여부 |
| Hot만 READY, tar 없음 | Hot은 시드 재사용. Cold+exactReuse로 재시도 |
| 포트 충돌 | `:8000` 사용 중 프로세스 종료 |

중지:

```bash
docker compose -f docker-compose.local.yml down
```

---

## 다음에 할 일 (아직 아님)

- Windows Factory 실빌드  
- Nexus push  
- Jenkins / 로그인·크리덴셜  
- K8s(k9s) 배포  

관련: `docs/airgap-docker-image-factory-tutorial.md` (폐쇄망 + Nexus, 이후 단계)
