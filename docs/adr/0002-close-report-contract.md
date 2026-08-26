---
유형: 의사결정기록
영역: 도구
사업: 법인
갱신일: 2026-08-27
태그: [도구, 런타임]
---

# close 계약을 값 보고로 — CloseReport

## 배경

#46(feat/46-close-reports-unreaped-child) 이전의 cancel() 폴백 경로는 terminate_process_only() 의 반환값 — 직접 킬이 자식을 실제로 거뒀는가 — 을 버렸다. 대기 만료로 False 가 나와도 close() 는 성공한 close 와 같은 모양(`None`) 으로 돌아왔고, 스펙 §4 불변식이 약속하는 «반환 시 잔여 0» 을 자식이 살아 있는 채로 주장했다. 코드는 #46 에서 `CloseReport` 값 보고로 고쳐졌고(02bd5f2), 대표가 갈래 A — 코드 유지·스펙 개정 — 로 결정했다(2026-08-26).

## 결정

### close 는 CloseReport 를 반환한다

- **날짜**: 2026-08-26
- **정한 것**: `AgentRuntime.close()` 는 frozen dataclass `CloseReport(child_processes, drain_tasks, reasons)` 를 반환한다. 아무것도 남지 않으면 개수 0과 빈 `reasons` 로 조용하고, 남으면 개수를 말한다. 사유는 코드가 관측해 보관한 실패에 한해 적는다: killer 부재(cleanup failure)와 직접 킬 미거둠(fallback reap failure)은 별개 항목으로 둘 다 적고, 트리 킬이 예외 없이 미확인으로 끝난 잔여는 사유 항목 없이 개수로만 드러난다.
- **대신 버린 것**:
  - **예외로 알리기** — `close_all_runs` 가 return_exceptions=True 로 전 run 종료를 보장하는 것과 충돌하고, 미래 broker shutdown 을 try/except 로 강제한다.
  - **내부 기록만 남기기** — 관찰자 기록만으로는 외부 게이트가 사실을 보지 못한다(#46 변이 실험에서 내부 필터와 외부 0-게이트가 서로를 가렸다).
  - **코드 되돌리고 스펙 유지** — 거짓 0 보고라는 결함을 규범화하는 길이라 대표가 기각했다(갈래 B).
- **왜**: 이 저장소의 경계 성실성 어휘는 값이다 — `RuntimeStatus`(hard/cooperative/unsupported), `CancelOutcome`, `RunOutcome`(unknown_outcome). #15 는 «확정 못 하면 성공으로 추정하지 않는다», #17 은 «측정 안 한 상한을 청구하지 않는다», #34 는 «정리 실패가 정리를 요청한 이유를 덮지 않는다»로 같은 원칙을 이미 골랐다. close 도 그 계보에 맞춰 «거두지 못했으면 0 이라고 말하지 않는다».
- **뒤집는 조건**: provider 가 shutdown receipt 를 공식 제공해 잔여 확인이 provider 계약에 들어오거나, broker shutdown 구조가 예외 전파를 요구하게 될 때 새 ADR 로 검토한다.

## 관련 기록

- docs/spec/whole-life-v0.md §4 — 같은 커밋에서 개정된 규범 문언
- GitHub issues #15 · #17 · #34 · #46
