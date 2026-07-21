# 인터넷망 로컬 검증 (단순)

이 문서는 **[local-verify-tutorial.md](./local-verify-tutorial.md)** 로 통합되었습니다.

로그인·Jenkins·Nexus 없이 포털 → 로컬 이미지 빌드/저장까지 확인하려면 위 문서를 보세요.

```bash
docker compose -f docker-compose.local.yml up --build
# http://127.0.0.1:8000/
```
