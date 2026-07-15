# 설계 개정 2 변경 요약

Capability 매칭 변경안과 1차 검토 피드백을 반영한 문서:  
[`web-based-on-demand-msbuild-image-factory-design.md`](./web-based-on-demand-msbuild-image-factory-design.md)

## 주요 변경

| 항목 | 이전(개정 1) | 개정 2 |
|------|--------------|--------|
| 이미지 재사용 | `profileHash` 완전 일치만 | Exact 우선 + **Capability ⊆ Superset** + 점수 |
| Hash 역할 | 재사용의 유일 키 | 요청 식별·CREATING Lock·감사·재현 |
| Profile 모델 | 단일 normalized profile | Requested / Capability / Build Input 3종 |
| API | hash·imageStatus 중심 | `matchType`, requested/matched hash, extraCapabilities |
| 설치 원본 | SDK SHA 위주 | Targeting Pack·Windows SDK·Layout manifest까지 핀 |
| Push | 검증 후 READY(모호) | staging → 검증 → final push → Capability DB 기록 |
| Callback | lease TTL | **leaseId CAS**로 stale 콜백 차단 |
| Factory 위치 | VM 또는 Builder 노드 | **전용 Windows 호스트** 명시 (K8s DinD 비권장) |
| 재현 빌드 | DEPRECATED 정책만 | `reuseMode: exactReuse \| preferCompatible` |
| 데이터 모델 | build_image 중심 | + `build_image_capability`, match 필드, partial unique |

## 구현 가능 여부 (재검토)

- **Exact + 주문형 Factory 경로:** 설계로 구현 가능.
- **Capability Superset 재사용:** 개정 2에 Matcher·스키마·API·감사 필드가 포함되어 요청 시나리오와 정합.
- **잔여 리스크:** Windows 이미지 크기/Pull SLA, Layout·독립 installer 반입 품질, Superset 부작용(여분 toolset) — 문서 §8.5·§20·단계 전략으로 완화.

## MVP 권고

1단계에서 Factory를 열지 않고 **Hot Preset + Capability Matcher**만으로 재사용을 검증한 뒤, 2단계에서 Cold 최소 집합 Factory를 연다.
