# msbuild-factory

Web 기반 주문형 MSBuild Docker 이미지 팩토리.

## 문서

- [설계서 (개정 2)](docs/web-based-on-demand-msbuild-image-factory-design.md)
- [개정 2 변경 요약](docs/design-revision-2-changelog.md)
- [Factory Host Runbook](docs/factory-host-runbook.md)
- [Jenkins Wiring](jenkins/WIRING.md)
- [K8s Portal + Postgres](k8s/portal/README.md)
- [K8s Windows workers](k8s/windows/README.md)

## 코드

- [Portal](portal/README.md) — FastAPI Backend + React UI + local simulation
- [Jenkins](jenkins/README.md) — Job pipelines + Portal agent scripts + CASC
- [K8s Portal](k8s/portal/README.md) — Portal Deployment + Postgres StatefulSet + Nexus registry env
- [K8s Windows](k8s/windows/README.md) — Windows node labels + pod templates

## 인프라 전제

| 구성 | 형태 |
|------|------|
| Portal | K8s Deployment (microservice pod) |
| PostgreSQL | 별도 StatefulSet/파드 |
| Jenkins | K8s 파드 (+ Linux agent / K8s cloud) |
| Nexus | Docker hosted registry (factory `docker push`) |
| Image Factory | 전용 Windows 호스트 (DinD 아님) |
| Project build | K8s Windows worker pod |
