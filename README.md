# msbuild-factory

Web 기반 주문형 MSBuild Docker 이미지 팩토리.

## 문서

- [설계서 (개정 2)](docs/web-based-on-demand-msbuild-image-factory-design.md)
- [개정 2 변경 요약](docs/design-revision-2-changelog.md)

## 코드 (MVP 착수)

- [Portal](portal/README.md) — FastAPI Backend + React UI
  - Profile Hash / Capability Matcher / Options·Validate·Build Request API
  - Hot Preset 시드 이미지 (Factory 미개방)
