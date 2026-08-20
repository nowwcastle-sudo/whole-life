---
유형: 의사결정기록
영역: 도구
사업: 법인
갱신일: 2026-08-20
태그: [도구, 지식관리]
---

# Whole Life 로컬 구독형 v0 결정

## 결정의 전제

현재 709줄 조사본은 A2A·MCP·AG-UI 기반 목표 아키텍처와 로컬 구독 CLI 기반 MVP를 한 문서에 함께 두어 구현 정본으로 사용할 수 없다. Codex 독립감사와 Claude Fable 5 독립·비교감사에서 이 결론과 구현 차단 finding이 상호 확인됐다.

### 구현 정본 분리

- **날짜**: 2026-08-20
- **정한 것**: 조사본을 수정하지 않고 비규범 자료로 보존하며, [[2026-08-20_Whole-Life_v0_규범설계]]을 v0의 유일한 구현 정본으로 둔다.
- **대신 버린 것**: 조사본 후반에 보정 문단을 더 붙이는 방식, 조사본 앞부분을 직접 삭제·재작성하는 방식.
- **왜**: 서로 다른 변경 이유를 가진 시장·표준 조사와 구현 계약을 한 파일에 두면 다시 dual MVP가 생긴다. 원문 보존은 감사 기준선과 provenance도 지킨다.
- **뒤집는 조건**: 없음. 새 version은 새 규범 문서와 ADR로 대체하고 조사본은 계속 보존한다.

### 로컬 구독 CLI v0

- **날짜**: 2026-08-20
- **정한 것**: 한 사용자의 Windows PC에서 공식 Claude Code·Codex CLI를 각자의 구독 로그인으로 실행하는 personal local broker를 v0로 만든다.
- **대신 버린 것**: provider API 우선 구조, A2A/MCP/AG-UI 기반 remote control plane, 다른 사용자의 subscription credential을 중계하는 서비스.
- **왜**: 현재 검증할 문제는 서로 다른 두 공식 CLI의 context·event 협업이고, API·remote 표준은 v0 성공에 필요하지 않다. 구독 credential을 복사·중계하지 않는 경계가 더 단순하고 설명 가능하다.
- **뒤집는 조건**: multi-user 제품화, remote agent, 제3 provider가 실제 요구가 되거나 provider 정책이 local subscription mode를 허용하지 않을 때 API/enterprise credential 구조를 별도 검토한다.

### broker 단일쓰기

- **날짜**: 2026-08-20
- **정한 것**: 두 AI는 read-only로 실행하고 broker의 `ArtifactCommitter`만 새 artifact를 쓴다.
- **대신 버린 것**: AI별 worktree 직접쓰기, native tool을 중앙 Tool Gateway가 사전승인한다고 주장하는 구조.
- **왜**: native CLI 내부 tool을 broker가 완전히 intercept한다는 근거가 없고, 단일쓰기가 권한·복구·감사·유지보수에서 가중평가 86점으로 worktree 81점보다 높았다.
- **뒤집는 조건**: read-only·process 종료·duplicate resume·crash recovery conformance가 통과하고, agent direct edit의 별도 권한모델을 승인할 때 worktree 기능을 새 ADR로 검토한다.

### 깊은 module 네 개와 stdlib-first

- **날짜**: 2026-08-20
- **정한 것**: 실제 seam인 `AgentRuntime`, concrete `Journal`, `ArtifactCommitter`, `DossierExporter`만 깊게 만들고 Python 표준 라이브러리로 시작한다.
- **대신 버린 것**: Event Bus·Projector·Read Model·Tool Gateway·Policy Broker·Context Broker service·Registry·UI Gateway, FastAPI·Redis·NATS·Kafka·ULID·outbox.
- **왜**: 두 실제 provider 차이와 process·storage·file commit 복잡성은 작은 interface 뒤에 숨길 가치가 있다. 나머지는 삭제해도 현재 복잡성이 호출자에게 재등장하지 않는 가상 seam이다.
- **뒤집는 조건**: 두 번째 실제 implementation·process writer·web 사용자 요구가 나타나 deletion test에서 복잡성이 호출자에게 번질 때만 해당 seam을 추출한다.

### event 관찰 실시간·context 턴 경계

- **날짜**: 2026-08-20
- **정한 것**: native event는 즉시 관찰하되 AI 간 context는 완료된 turn 다음의 새 turn 시작에서만 전달한다.
- **대신 버린 것**: mid-turn context push, prompt 전달을 모델 적용 acknowledgment로 간주하는 방식.
- **왜**: 현재 공식 CLI의 안정된 streaming·resume 표면으로 측정 가능하고, 모델 내부 적용을 과장하지 않는다.
- **뒤집는 조건**: provider가 공식 mid-turn input과 receipt를 안정적으로 제공하고 실패 의미가 실측될 때.

### 두 플랫폼·복수 참여 agent·자율 native delegation

- **날짜**: 2026-08-20
- **정한 것**: platform adapter 종류는 Claude·Codex 둘로 고정하되 session roster에는 양쪽을 최소 1명씩 포함한 2~8명의 동등한 participant를 둘 수 있게 한다. 각 participant는 독립 native session에서 read-only native worker를 한 단계로 자율 병렬 실행한다. Codex worker concurrency의 절대 안전 상한은 3개 hard cap·depth는 cooperative, Claude concurrency의 절대 안전 상한은 3개 cooperative budget·depth는 hard로 기록하고, 실제 turn budget은 token profile이 그 이하로 정한다. 모든 participant에 20분 turn hard timeout을 적용한다.
- **대신 버린 것**: Claude 1명+Codex 1명 고정, native worker를 broker의 1급 participant로 승격하는 방식, 무제한·재귀 spawn, Claude experimental agent teams.
- **왜**: 여러 관점과 병렬 실행력을 얻으려면 1급 협업 agent 수와 각 provider harness 내부 worker 수를 분리해야 한다. Claude subagent는 parent에게만 결과를 돌려주고 재귀 spawn을 지원하지 않으며, Codex도 공식 subagent workflow의 parent sandbox를 상속한다. 최소 공통 의미를 depth 1·read-only로 맞추면 broker가 보장할 수 없는 peer messaging이나 내부 context 공유를 약속하지 않으면서도 각 participant의 자율 분해 능력을 쓸 수 있다. Claude stable CLI에는 Codex와 같은 숫자 hard cap이 공식 확인되지 않았으므로 둘을 같은 보장으로 기록하지 않는다.
- **뒤집는 조건**: Claude agent teams가 stable이 되고 양 provider가 nested delegation·resume·shutdown·event identity를 동등하게 공식 지원하며 runaway·orphan·권한상속 test를 통과할 때 depth와 peer coordination을 새 ADR로 검토한다.

### 결정론적 dossier

- **날짜**: 2026-08-20
- **정한 것**: 첫 산출물은 원 답변·상호비평·검증된 공통사실·미해결 충돌·사용자 결정·provenance를 고정 순서로 보존하는 Markdown이다.
- **대신 버린 것**: 세 번째 AI가 두 결과를 임의 합성한 최종답.
- **왜**: 합성은 반대 의견과 근거를 조용히 없앨 수 있다. 동일 event log에 byte-identical 결과를 만드는 exporter가 감사·재현에 유리하다.
- **뒤집는 조건**: dossier를 보존한 뒤 별도 파생 요약이 필요하다는 실제 사용자 요구가 생길 때에만 비규범 view로 추가한다.

### token-aware progressive activation과 capsule 교환

- **날짜**: 2026-08-20
- **정한 것**: session roster는 2~8명을 보존하되 기본 `balanced`는 첫 Claude·Codex 두 명만 seed로 실행한다. 두 seed의 capsule 비평에서 충돌·근거누락·낮은 확신·추가 agent 필요가 구조화 field로 보고될 때만 standby 최대 2명을 깨운다. participant 간에는 `full_answer`가 아니라 profile별 4/8/16 KiB 상한의 `handoff_capsule`만 전달하고, user가 전원 실행을 명시적으로 원할 때만 `deep`을 쓴다.
- **대신 버린 것**: roster 전원 상시 실행, 모든 participant의 원문 all-to-all 교환, 별도 AI가 context를 요약하거나 실행 규모를 결정하는 방식, provider token을 subscription quota·청구금액으로 환산하는 방식.
- **왜**: agent 수·round 수·native worker 수·상호 원문 길이는 서로 곱해져 사용량을 폭증시킨다. broker가 통제할 수 있는 것은 provider 청구식이 아니라 process 수·turn 수·worker budget·교환 bytes다. seed 우선 실행과 deterministic escalation은 불필요한 호출을 없애고, capsule은 원문을 dossier에 보존하면서 cross-agent context만 제한한다. 별도 summarizer는 토큰을 절약하기 위해 다시 토큰을 쓰는 모순이므로 두지 않는다.
- **뒤집는 조건**: 실제 workload telemetry에서 capsule 때문에 중요한 반증이 반복 누락되거나, provider가 공식 quota budget·server-side shared context·cache control을 안정적으로 제공하고 conformance를 통과할 때 profile 수치와 projection topology를 새 ADR로 조정한다.

### private-first와 public fail-closed

- **날짜**: 2026-08-20
- **정한 것**: 설계 gate 통과 뒤 `nowwcastle-sudo/whole-life` private 저장소를 만들고, 정책·usage 귀속·secret gate 통과 뒤에만 public 전환을 검토한다.
- **대신 버린 것**: 즉시 public, 구독 지원 가능성을 정책 허용으로 단정하는 README.
- **왜**: 기술적 subprocess 실행 가능성과 provider 정책상 공개 제품 허용은 다른 명제이고, push·공개는 되돌리기 비용이 크다.
- **뒤집는 조건**: 최신 공식 문서 또는 서면 답변, 실제 usage 귀속 smoke test, secret scan이 모두 통과할 때 public 전환 ADR을 새로 쓴다.

## 관련 기록

- [[2026-08-20_Whole-Life_v0_규범설계]]
- [[2026-08-20_Whole-Life_아키텍처_정밀감사_Codex]]
- [[2026-08-20_Whole-Life_아키텍처_교차검증_Claude]]
