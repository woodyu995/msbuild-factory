# 로컬 / 폐쇄망 단계 검증

목표: **로그인 / Jenkins / Nexus 없이** 포털에서 환경을 고르면  
이미지가 **빌드·저장**되고 결과(digest / tar)만 확인한다.

| 환경 | 문서 |
|------|------|
| **폐쇄망 + 실제 Server Core / VS Build Tools** | **[airgap-windows-factory-tutorial.md](./airgap-windows-factory-tutorial.md)** ← 목표 경로 |
| 폐쇄망 + 검증용 stub만 (`FROM scratch`) | [airgap-local-verify-tutorial.md](./airgap-local-verify-tutorial.md) |
| 인터넷망에서 stub 바로 확인 | 아래 짧은 절차 |

```text
브라우저 → Portal → Ensure → 로컬 docker build + docker save → READY
                 ↓
         ./local-images/<tag>.tar
         docker images msbuild-local
```

> 검증용 stub 이미지입니다. 실제 Windows MSBuild + Nexus는 이후 단계입니다.

---

## 인터넷망에서 바로 (참고)

```bash
git checkout cursor/portal-mvp-implementation-03e9
docker compose -f docker-compose.local.yml up --build
# http://127.0.0.1:8000/
```

1. Reuse mode = `exactReuse` + Cold 조합 (예: C++ v143 + MFC)  
2. **Ensure image** → READY  
3. `ls ./local-images/` / `docker images msbuild-local`

---

## 폐쇄망

USB 반입 → load → compose → Ensure → tar 확인까지는  
**[airgap-local-verify-tutorial.md](./airgap-local-verify-tutorial.md)** 를 따르세요.

```bash
# 폐쇄망 호스트 (이미지 load 후)
docker compose -f docker-compose.airgap-local.yml up -d
```
