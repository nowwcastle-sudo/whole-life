---
유형: 작업기록
영역: 도구
사업: 법인
작성일: 2026-08-22
태그: [작업기록, 도구, 지식관리]
---

# Gate 2 — 구독 사용량 귀속 smoke test 설계

> **상태:** 설계만 확정됐다. 실행은 실제로 돌릴 수 있는 testbed를 만든 뒤로 미룬다(2026-08-22 소유자 결정). 이 문서는 그때 그대로 따라 하기 위한 절차서이며, 실행 결과는 여기에 적지 않는다.

`docs/project-context.md`의 public-release gate 2 — “두 CLI의 실제 usage 귀속 smoke test 통과” — 를 무엇으로 통과 처리할지 정한다. 사양 [§5 과금 귀속 한계](../spec/whole-life-v0.md)가 “auth preflight는 청구 원장의 증거가 아니다”라고 선을 그어 둔 그 빈자리를 이 test가 채운다.

## 1. 무엇을 증명하려는가

한 덩어리로 “잘 되는지” 보지 않고, 서로 독립적으로 반증 가능한 명제 셋으로 나눈다. 하나라도 흐릿하게 남으면 gate 2는 통과가 아니다.

| | 명제 | 반증되는 모습 |
|---|---|---|
| **P1** | broker가 구동한 CLI는 API key 경로로 조용히 넘어가지 않는다 | API key가 있는 환경에서도 실행이 성공한다 |
| **P2** | 실행분이 해당 구독의 사용량 카운터에 잡힌다 | 실행 전후로 카운터가 움직이지 않는다 |
| **P3** | 같은 실행으로 별도 API 청구가 발생하지 않는다 | provider 콘솔의 API usage에 해당 시각 호출이 찍힌다 |

P1을 먼저 세우지 않으면 P2·P3를 측정할 수 없다. 실행이 성공했다는 사실만으로는 **구독으로 성공한 것**과 **API key로 성공한 것**을 구분할 방법이 없기 때문이다.

## 2. T-0 — 환경 고정

- pinned version을 기록한다. 설계 시점 실측 기준선은 Claude Code `2.1.239`, `codex-cli 0.149.0`이다. 다른 version으로 돌렸으면 그 version을 적고, 기준선 결과를 그대로 쓰지 않는다.
- 사양 §5의 child environment builder를 그대로 쓴다. 사람이 손으로 만든 임시 환경에서 측정하면 실제 broker 실행과 다른 것을 재게 된다.
- **API key를 쓸 수 있는 계정으로 측정한다.** API key가 애초에 없는 계정이면 P3는 자동으로 참이 되어 아무것도 증명하지 못한다. Anthropic·OpenAI 양쪽에 활성 결제 수단과 API key가 존재하는 상태여야 P3가 의미 있는 검사가 된다.
- **측정 창 동안 같은 계정으로 다른 Claude·Codex 작업을 하지 않는다.** 에디터에 붙어 있는 세션, 백그라운드 agent, 다른 채널의 자동화가 모두 같은 카운터를 움직인다. 이 격리가 깨지면 P2의 delta는 해석 불가가 된다.

## 3. T-1 — 정적 인증 증거 (토큰 소비 0)

정제한 child env에서 두 CLI의 auth 상태를 읽고 사양 §5 성공 조건과 대조한다.

- Claude: `claude auth status --json`이 `loggedIn`, `authMethod`, `apiProvider`, `subscriptionType`을 §5가 요구하는 값으로 반환하는지 확인한다. 같은 출력의 `email`·`orgId`·`orgName`은 읽지도 기록하지도 않는다.
- Codex: `codex login status`가 ChatGPT 로그인 상태와 정확히 일치하고 exit 0인지 확인한다.
- argv에 `--bare`가 없고 child env에 `CLAUDE_CODE_SIMPLE`이 없는지 확인한다(사양 §5 bare mode gate).

여기까지는 아무 요청도 보내지 않으므로 몇 번 돌려도 비용이 없다. 뒤 단계를 돌리기 전에 반드시 통과시킨다.

## 4. T-2 — 음성 통제 (P1)

**측정보다 먼저 한다.** 여기서 실패하면 T-3의 성공은 아무 의미가 없다.

| 주입 | 기대 결과 |
|---|---|
| child env에 더미 `ANTHROPIC_API_KEY` | preflight가 기동 거부 |
| child env에 더미 `OPENAI_API_KEY` | preflight가 기동 거부 |
| child env에 `CLAUDE_CODE_SIMPLE=1` | preflight가 기동 거부 |
| argv에 `--bare` 강제 | preflight가 기동 거부 |

더미 값은 형식만 맞는 합성 문자열을 쓴다. 진짜 key를 주입하지 않는다 — 실패 경로가 그 값을 로그·진단 메시지로 흘릴 수 있고, 그건 되돌릴 수 없다.

마지막 두 줄은 사양 §5 bare mode gate의 conformance fixture와 같은 것이다. 이 test를 돌리면 그 fixture가 함께 만들어진다.

추가로, gate를 **끈** 상태에서 bare mode를 한 번 실행해 구독 로그인만으로는 성립하지 않는다는 것을 눈으로 확인한다. bare mode가 정말 OAuth를 읽지 않는다는 전제 자체를 실측으로 확인하는 유일한 단계다.

## 5. T-3 — 귀속 측정 (P2)

각 provider마다 **실행 직전 스냅샷 → 최소 turn 1회 → 실행 직후 스냅샷** 순서로 한다.

- 최소 turn은 고정 prompt·고정 model·tool 없음으로 한다. 재실행할 때 같은 조건이어야 delta를 비교할 수 있다.
- Claude는 `-p --output-format json`으로 돌리고 결과 envelope의 `usage`, `modelUsage`, `total_cost_usd`, `stop_reason`을 기록한다.
- Codex는 `codex exec --json`으로 돌리고 JSONL의 token 관련 event를 기록한다.
- 구독 카운터 스냅샷은 provider가 사용자에게 보여 주는 화면에서 읽는다. 비대화식으로 사용량을 조회하는 subcommand는 두 CLI 모두에 없다(2026-08-22 실측). 사람이 화면을 읽어야 하며, **이것이 testbed가 필요한 이유이자 이 test를 자동화로 미룰 수 없는 이유다.**

### ⚠️ 여기서 가장 쉽게 오독한다

`total_cost_usd`는 **구독 귀속의 증거가 아니다.** 구독으로 실행해도 list price 환산 추정치가 찍힐 수 있어서, 이 값이 0이 아니라는 것을 “API로 과금됐다”로 읽으면 틀린다. 반대로 0이라는 것을 “구독으로 처리됐다”로 읽어도 틀린다. 사양 §8의 usage ledger 규정 — provider token 필드는 nullable telemetry이지 quota·billing이 아니다 — 이 여기에도 그대로 적용된다.

**1차 증거는 provider 쪽 구독 카운터의 delta 하나뿐이다.** CLI가 내보내는 숫자는 전부 보조 기록으로만 남긴다.

## 6. T-4 — API 청구 부재 (P3)

- 실행 후 **24~48시간 뒤에** Anthropic Console과 OpenAI Platform의 usage·billing 화면에서 해당 시각대의 API 호출이 0건인지 확인한다.
- 즉시 확인하지 않는다. 두 대시보드 모두 집계 지연이 있어서, 실행 직후의 “0건”은 통과가 아니라 아직 안 보이는 것일 수 있다. 지연을 기다리지 않은 확인은 거짓 통과다.

## 7. T-5 — 재현

T-3을 다른 날 한 번 더 돌린다. 1회 측정만으로는 관측한 delta가 이 실행 때문인지 같은 시간대의 다른 사용 때문인지 가를 수 없다. 두 번의 delta가 같은 크기로 재현될 때만 P2를 통과로 본다.

## 8. 합격 기준

| 명제 | 통과 조건 |
|---|---|
| P1 | T-2의 네 주입이 **모두** 기동 거부. 하나라도 실행에 성공하면 실패 |
| P2 | T-3의 구독 카운터 delta가 실행 1회에 상응하고, T-5에서 재현됨 |
| P3 | T-4에서 해당 시각 API 호출 0건 (집계 지연 경과 후) |

셋을 모두 통과해야 gate 2가 닫힌다. 부분 통과는 통과가 아니라 미확정이다.

## 9. 실패했을 때

- **T-2 실패** — P0. broker의 구독 전제가 성립하지 않는다. 구현을 진행하지 않고 사양 §5를 먼저 고친다.
- **T-3 불명확** — 통과로 올리지 않고 미확정으로 남긴다. 사양 §5 과금 귀속 한계에 따라 README에 구독 과금·귀속 보장 문구를 쓰지 않는다.
- **T-4에서 청구 발생** — P0. 어느 경로로 샜는지 특정하기 전까지 실행을 중단한다.
- **pinned version이 bare mode 기본으로 바뀜** — gate 2 실패가 아니라 사양 §5 bare mode gate가 잡아야 할 별개 사건이다. 해당 version을 allowlist에서 제외하고 재측정한다.

## 10. 기록물

- 결과는 통과·불통과와 pinned version만 이 저장소에 남긴다.
- 카운터 화면 캡처에는 계정 식별자와 사용 이력이 함께 찍힌다. 저장소에 넣지 않는다. `.audit/`는 `.gitignore` 대상이며, 원문은 저장소 밖에 둔다.
- 더미 key를 포함한 어떤 credential 값도, 성공·실패 어느 경로에서도 기록에 남기지 않는다.
