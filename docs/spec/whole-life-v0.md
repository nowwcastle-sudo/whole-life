---
유형: 작업기록
영역: 도구
사업: 법인
작성일: 2026-08-20
태그: [작업기록, 도구, 지식관리]
---

# Whole Life v0 규범 설계

> **규범 상태:** 이 문서가 Whole Life v0 구현의 유일한 정본이다. [[2026-08-20_AI실시간협업_아키텍처_기초설계]]은 시장·표준·목표 아키텍처를 조사한 **비규범 자료**이며, 두 문서가 충돌하면 이 문서가 우선한다.

## 1. 문서 통제

| 항목 | 값 |
|---|---|
| 표시명 | Whole Life |
| Python package | `whole_life` |
| 로컬 저장소 예정 경로 | `D:\whole-life` |
| GitHub 예정 저장소 | `nowwcastle-sudo/whole-life` |
| 앱 데이터 | `%LOCALAPPDATA%\WholeLife` |
| 라이선스 | Apache-2.0 |
| 대상 OS | Windows-first; macOS/Linux는 v0 이후 |
| 배포 상태 | 설계 gate 통과 뒤 private, 정책 gate 통과 뒤에만 public 검토 |
| 기준 조사본 | SHA-256 `C243DB1EF4C27662872C17E85350883C5663943D4C1F13F1D6D3F977643F831F`, 709줄 |
| Codex 원본 task | `01a0195a-930d-7533-90df-b3627c68a440` |
| Claude 교차검증 session | `1c2b60bc-7764-420f-92bc-fa3446fac9a9` |
| 개정 | 2026-08-22 — §5에 bare mode gate와 실측 auth schema, §12 인증 conformance, §13 F-16 추가 |

이 문서는 구현 코드가 아니다. 여기서 “합격”은 interface·상태·실패 경계가 구현 가능한 수준으로 결정됐다는 뜻이며, 실행 코드의 무결점 판정을 뜻하지 않는다.

## 2. v0 목표와 비목표

### 목표

한 사용자가 자기 Windows PC에서 자기 ChatGPT·Claude 구독으로 로그인한 공식 CLI를 로컬 broker가 실행한다. 플랫폼 종류는 Claude와 Codex 두 개로 고정하지만, 한 협업 session에는 서로 독립된 native session을 가진 참여 agent를 2명 이상 편성할 수 있다. 참여 agent들의 원 답변과 비평을 턴 경계에서 교환하고, 원문·합의·충돌·provenance를 잃지 않는 결정론적 dossier를 만든다. 기본 profile은 필요한 두 participant만 먼저 실행하고 충돌이 있을 때만 추가 participant를 깨우며, 원문 전체가 아니라 크기가 제한된 구조화 capsule을 전달해 roster 크기가 그대로 토큰 사용량이 되지 않게 한다.

### agent 계층과 수량

- **협업 참여 agent(`participant`)**: broker가 roster에 등록한 1급 행위자다. 독립 `participant_id`, provider, role, native session을 가지며 다른 participant의 확정 결과를 다음 round에 받는다.
- **provider-native worker(`native worker`)**: participant가 자기 turn 안에서 Claude `Agent` 또는 Codex subagent workflow로 만든 임시 worker다. parent participant에게 결과를 돌려주지만 broker의 1급 participant가 아니며 다른 플랫폼과 직접 대화하지 않는다.
- session roster는 2~8 participant다. Claude와 Codex participant가 각각 최소 1명 있어야 한다. roster에 있다는 사실과 해당 task에서 즉시 실행되는 `active` 상태를 구분한다.
- broker는 user가 준 ordered roster 안에서 provider별 1-based ordinal을 부여해 `claude-01`, `codex-01` 형식의 unique `participant_id`를 만든다. 같은 roster 입력은 같은 ID와 canonical order를 만든다.
- user가 session 시작 전에 `economy`, `balanced`, `deep` 중 하나를 고른다. 기본값은 `balanced`이며 broker가 별도 model call로 profile을 추정하지 않는다.
- `economy`와 `balanced`는 roster의 첫 Claude·첫 Codex participant를 seed로 실행한다. 나머지는 ordered standby다. `deep`만 roster 전체를 처음부터 active로 실행한다.
- 동시에 실행하는 participant turn은 profile과 관계없이 최대 4개다. 나머지는 broker queue에서 기다린다.
- native worker budget은 profile·round가 정한다. provider가 total-start hard cap을 제공하지 않으면 prompt policy와 stream 관찰에 의한 `cooperative` enforcement로 기록하고, 초과 활동을 관찰하면 cancel한다.
- v0 native delegation depth는 1이다. participant는 worker를 만들 수 있지만 worker는 다시 worker를 만들 권한이 없다. 이 상한은 broker가 지킨다. 2.1.240 실측에서 Claude는 worker가 만든 worker를 거부하지 않았으므로, broker가 `spawn_depth` 관찰로 위반을 감지하고 cancel한다.
- 모든 participant turn은 broker wall-clock 20분 hard timeout을 가진다. cooperative budget 초과 활동을 관찰하면 즉시 cancel하고 결과를 `unknown_outcome`으로 둔다.
- roster·profile별 active turn·worker·round·output·capsule·projection·timeout 수치는 runaway process·구독 사용량·context 폭증을 막는 v0 고정 상한이다. 임의 세부 설정으로 일반화하지 않고 실제 conformance 측정 뒤 ADR과 test를 함께 바꾼다.

“각 agent의 실행 권한”은 위 participant가 자기 판단으로 읽기 전용 조사·검증을 native worker에 위임하고 병렬화할 권한을 뜻한다. file write, shell, 외부 side effect 권한을 뜻하지 않으며 artifact 쓰기는 계속 broker만 수행한다.

### 실시간의 정확한 의미

- **이벤트:** adapter가 native event를 수신한 시점부터 broker subscriber에 전달할 때까지 실시간으로 관찰한다.
- **AI 간 컨텍스트:** 진행 중인 turn에 주입하지 않는다. 완료된 상대 turn을 다음 turn 시작 prompt의 untrusted data block으로 전달한다.
- **적용 확인:** prompt에 넣었다는 사실만 기록한다. provider가 공식 acknowledgment를 주지 않는 v0에서는 `delivery_unverified`이며, 모델이 이해·적용했다고 주장하지 않는다.

### v0 비목표

- 여러 사용자의 계정·구독 credential을 중계하는 서비스
- mid-turn context push, running session attach, checkpoint, approval callback
- AI가 파일을 직접 수정하는 worktree 협업
- native worker를 독립 participant로 승격하거나 플랫폼 간 직접 연결하는 기능
- recursive delegation(depth 2 이상)
- Claude experimental agent teams와 그 shared task list·peer messaging
- native tool 실행을 broker가 사전 승인했다는 보장
- remote agent, multi-node, multi-process writer, web UI
- A2A, MCP orchestration, AG-UI, CloudEvents SDK
- FastAPI, Redis, NATS, Kafka, transactional outbox, CRDT, lease/fencing
- ULID와 동적 plugin/adapter registry
- AI가 최종답을 다시 합성하는 별도 judge turn
- provider 구독 quota나 청구액을 토큰 수만으로 정확히 예측·우회하는 기능
- unrelated task 사이에서 native session history를 재사용해 context를 무한 누적하는 기능

## 3. v0 책임과 module 경계

v0는 한 Python process 안의 다음 책임만 가진다.

1. `Broker`: immutable participant roster, token profile, deterministic activation·review schedule, round·turn queue, native session lock, global/participant delegation budget, projection 구성, runtime과 Journal 조정.
2. `AgentRuntime`: 실제 Codex·Claude CLI 두 adapter implementation 뒤에 인증, process, stream, resume, native delegation 차이를 숨기는 유일한 provider seam. participant 수만큼 run/session handle을 만들 수 있지만 adapter implementation 종류는 두 개뿐이다.
3. `Journal`: concrete SQLite implementation 하나. event append, current run state 갱신, replay, commit 후 fan-out을 담당한다.
4. `ArtifactCommitter`: 허용 root 안의 no-overwrite artifact commit과 crash recovery를 담당한다.
5. `DossierExporter`: Journal의 확정 event를 고정 Markdown 형식으로 변환한다.

`Broker` 안의 roster·scheduler·projection 함수나 run registry는 별도 service/interface로 추출하지 않는다. `Journal`, `ArtifactCommitter`, `DossierExporter`도 두 번째 storage/export implementation이 생기기 전에는 추상 storage interface를 두지 않는다.

## 4. AgentRuntime 계약

```python
class AgentRuntime(Protocol):
    async def preflight(self) -> RuntimeStatus: ...
    async def start_turn(self, request: TurnRequest) -> RunHandle: ...
    def events(self, run: RunHandle) -> AsyncIterator[RuntimeEvent]: ...
    async def cancel(self, run: RunHandle) -> CancelOutcome: ...
    async def wait(self, run: RunHandle) -> RunOutcome: ...
    async def close(self) -> None: ...
```

### 불변식

- `TurnRequest.mode`는 `new` 또는 `resume`이다.
- `TurnRequest`에는 `participant_id`, `round_id`, `budget_profile`, `delegation_budget`, `result_limits`가 있고 immutable session roster·profile과 일치해야 한다.
- `resume`에는 provider가 발급한 `native_session_id`가 반드시 있고, 공식 resume 기능만 사용한다.
- 새 session에 이전 snapshot을 넣는 동작은 `new`이지 `resume`이 아니다.
- 같은 `(provider, native_session_id)`에는 active run 하나만 허용한다. 두 번째 start는 실행 전에 `ConcurrentResumeRejected`로 끝낸다.
- optional 동작을 `None`이나 모의 구현으로 처리하지 않는다. preflight capability가 없으면 `Unsupported`로 거부한다.
- `close()`가 반환될 때 이 runtime이 만든 child process와 stdout/stderr drain task는 0개여야 한다.

### native delegation 계약

- participant prompt에는 독립적으로 나눌 가치가 있고 추가 호출이 원 답변의 반복이 아니라 context 격리·독립 검증에 이득일 때만 native worker를 자율적으로 사용하라는 정책과 해당 turn의 남은 budget을 넣는다. budget이 0이면 `Agent`/subagent 기능은 노출하더라도 사용하지 말라는 cooperative policy를 명시하고 stream 위반을 감시한다.
- Claude participant에는 `Agent` 도구를 허용한다. Claude subagent는 parent context와 분리되고 결과를 parent에게 반환한다. v0는 experimental agent teams를 활성화하지 않는다.
- Codex participant에는 current release의 subagent workflow를 활성화하고 profile의 turn budget과 무관하게 안전 상한 `agents.max_concurrent_threads_per_session = 3`을 inline config로 고정한다. participant의 read-only sandbox를 spawned agent가 상속해야 한다.
- adapter는 native worker의 세부 reasoning이나 tool state를 완전하게 공유한다고 주장하지 않는다. provider stream이 공개하는 spawn·finish·summary metadata만 `runtime.activity.*`로 정규화한다.
- `RuntimeStatus`는 `worker_concurrency_enforcement`, `worker_total_start_enforcement`, `delegation_depth_enforcement`를 각각 `hard`, `cooperative`, `unsupported` 중 하나로 보고한다. v0 문서 기준 Codex는 concurrency `hard`·total starts `cooperative`·depth `cooperative`이지만, 이 값들은 문서에서 온 기대치이지 관측이 아니다. Codex 위임 측정은 아직 실행되지 않았으므로 runtime은 세 축을 모두 `unsupported`로 보고하고 fail-closed한다. 재측정 뒤 이 행을 확정한다. Claude는 concurrency `cooperative`·total starts `cooperative`·depth `cooperative`다. depth는 2026-08-23 실측으로 정정했다 — worker가 다시 worker를 띄운 turn이 거부 없이 성공했고 `subagent_stats.refused.depth_limit`이 0이었다. `spawn_depth`로 관측은 되므로 `unsupported`가 아니라 `cooperative`이며, depth 1은 broker가 지킨다. 구현 smoke test가 다르게 나오면 성공으로 추정하지 않고 사양·ADR을 먼저 갱신한다.
- provider가 native worker lifecycle 식별자를 공개하지 않으면 `native_child_id`는 비워 두고 `observability=summary_only`로 기록한다. broker가 임의 ID를 provider ID인 것처럼 만들지 않는다.
- provider stream에서 worker start 누계를 profile budget 위반 전에 셀 수 없으면 `worker_total_start_enforcement=unsupported`다. v0의 세 profile은 모두 native delegation 권한을 포함하므로 해당 runtime을 fail-closed하고 “상한을 지켰다”고 추정하지 않는다.
- participant의 `message.committed` 중 검증된 `handoff_capsule`만 다른 participant의 다음 round projection에 들어간다. `full_answer`와 native worker raw output은 플랫폼 간 prompt로 재주입하지 않는다.
- native delegation capability가 preflight 또는 smoke test에서 확인되지 않으면 해당 participant turn은 `DelegationUnsupported`로 fail-closed한다. 조용히 single-agent로 낮추지 않는다.

### v0 transport

| Runtime | 새 turn | resume | machine-readable output |
|---|---|---|---|
| Codex | `codex exec --json` + inline `agents.enabled=true`, `agents.max_concurrent_threads_per_session=3`, provider-side output schema가 지원되면 `--output-schema` | `codex exec resume <native_session_id>`에 동일 안전 옵션 적용 | JSONL |
| Claude | `claude -p --safe-mode --output-format stream-json --verbose`, `--tools Agent,Read,Glob,Grep`, provider-side output schema가 지원되면 `--json-schema` | `--resume <native_session_id>` | stream-json |

Claude argv에는 `--bare`를 **넣지 않는다.** 넣지 않는 것만으로는 부족하며, 최종 argv에 `--bare`가 없다는 것과 pinned version의 `-p` 기본 모드가 bare가 아니라는 것을 §5 bare mode gate가 실행 직전에 확인한다.

prompt는 command-line argument가 아니라 UTF-8 stdin으로 전달한다. executable은 시작 시 절대경로로 해석한다. Windows에서 PowerShell shim은 직접 실행 대상으로 쓰지 않고 실제 실행 가능한 `.cmd` 또는 `.exe`를 해석한다. provider-side schema option의 실제 지원 여부는 preflight로 확인하며, 지원되지 않아도 broker의 동일 JSON schema 검증은 생략하지 않는다.

broker release는 conformance를 통과한 Claude Code·Codex CLI exact version allowlist를 코드에 포함한다. preflight의 실제 version이 allowlist에 없으면 flag·stream 의미가 같다고 추정하지 않고 `UnsupportedCliVersion`으로 기동을 거부한다. 지원 version을 늘릴 때는 auth·safe argv·sandbox·tool allowlist·stream fixture를 해당 version에서 다시 통과시키고 새 release로 allowlist를 갱신한다.

### read-only 실행 경계

- Codex는 `--sandbox read-only`, `--ignore-user-config`, `--ignore-rules`를 사용하고 writable `--add-dir`을 주지 않는다. working snapshot에는 `AGENTS.md`, `.codex`, hook·MCP 설정 같은 control file을 복사하지 않는다.
- Claude는 `--safe-mode`, `--strict-mcp-config`, 빈 MCP config, `--tools Agent,Read,Glob,Grep`, `--permission-mode dontAsk`를 사용한다. user/project 설정·CLAUDE.md·plugin·hook을 로드하지 않고 `Write`, `Edit`, `Bash`, browser·network side-effect tool을 노출하지 않는다. Claude worker도 parent tool allowlist보다 넓은 권한을 받을 수 없다.
- Codex participant와 spawned agent는 모두 `--sandbox read-only`를 상속한다. custom agent file이 더 넓은 sandbox를 요구해도 parent runtime override가 우선해야 한다.
- runtime의 working directory는 broker가 지정한 읽기 전용 input snapshot root다. 앱 데이터·artifact root는 runtime working directory나 추가 접근 경로에 포함하지 않는다.
- `runtime.activity.*`는 native 읽기 활동의 **관찰 기록**이다. broker 승인·멱등 실행 증거가 아니다.
- write-like native activity가 stream에 나타나면 해당 turn을 실패 처리한다. 이 검사는 보조 탐지이며, 주된 통제는 CLI sandbox와 tool allowlist다.

## 5. 구독 인증 preflight

Whole Life의 v0 subscription mode는 각 공식 CLI의 기존 사용자 로그인을 실행할 뿐, credential 파일·token 값을 읽거나 복사하거나 저장하지 않는다.

### 공통 child environment

- 부모 process 환경은 변경하지 않는다.
- child env는 필요한 OS·locale·CLI 실행 변수의 allowlist로 새로 만든다.
- 다음 값은 존재 여부와 무관하게 child env에서 제거한다: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `CLAUDE_CODE_SIMPLE`.
- `CLAUDE_CODE_SIMPLE`은 API key 변수가 아니라 **인증 경로 자체를 바꾸는 mode switch**다. 값이 있으면 Claude Code는 `--bare`와 동일하게 동작하며 OAuth와 keychain을 읽지 않는다(§5 bare mode gate). API key 목록과 같은 줄에서 함께 제거한다.
- `CODEX_HOME`은 상속에 맡기지 않고 broker가 명시적으로 지정한다. `--ignore-user-config`는 `config.toml`만 무시하고 **인증은 그대로 `CODEX_HOME`에서 찾으므로**, 이 값이 흔들리면 auth 판정과 실제 실행이 서로 다른 credential store를 볼 수 있다.
- status와 실행 process는 정확히 같은 env builder를 사용한다.
- 환경값 원문, 전체 auth JSON, raw stderr는 Journal·artifact·vault에 저장하지 않는다.

### Claude 성공 조건

정제한 child env에서 `claude auth status --json`을 실행한 뒤 다음을 모두 만족해야 한다.

- 출력 parse 성공
- `loggedIn == true`
- `authMethod == "claude.ai"`
- `apiProvider == "firstParty"` — 이 필드가 provider 경로(first-party 구독 대 Bedrock/Vertex 등 제3자 credential)를 가른다
- `subscriptionType`이 존재하고 비어 있지 않음
- 알려지지 않은 auth schema, 위 값 중 하나라도 불일치, parse 실패, nonzero exit는 전부 fail-closed

version-pinned fixture로 필드 집합 자체를 고정한다. 필드가 사라지거나 새 값이 나타나면 성공으로 추정하지 않고 `AuthStatusUnsupported`로 중단한다. 예상 필드가 없는 상태에서 `loggedIn`만 보고 통과시키지 않는다.

같은 출력에는 `email`, `orgId`, `orgName`처럼 판정에 쓰이지 않는 계정 식별자가 함께 들어 있다. preflight는 위 네 개 판정 필드만 읽고, 나머지는 Journal·artifact·log·진단 메시지 어디에도 남기지 않는다.

### Codex 성공 조건

정제한 child env에서 `codex login status`가 version-pinned fixture의 ChatGPT 로그인 상태와 정확히 일치해야 한다. API key env가 없어야 하며, 출력 형식 변화·불일치·nonzero exit는 `AuthStatusUnsupported` 또는 `SubscriptionAuthRequired`로 중단한다.

### bare mode gate

Claude Code의 `--bare`는 hook·plugin·CLAUDE.md 같은 customization을 끄는 것에서 그치지 않고 **인증 경로를 바꾼다.** bare mode에서 Anthropic 인증은 `ANTHROPIC_API_KEY` 또는 `--settings`의 `apiKeyHelper`로 한정되며 OAuth와 keychain은 읽지 않는다. 즉 bare mode가 켜지는 순간 v0의 구독 전제가 성립하지 않는다.

customization을 끄는 목적으로는 `--safe-mode`만 쓴다. `--safe-mode`는 인증·model 선택·built-in tool·권한을 정상 동작시키므로 §4 read-only 실행 경계가 요구하는 것을 모두 만족한다. 두 flag를 같은 것으로 취급하지 않는다.

gate는 세 겹이다.

1. **argv** — 최종 argv에 `--bare`가 없음을 실행 직전에 assert한다. adapter가 argv를 만들 때 넣지 않는 것과, 만들어진 argv를 검사하는 것은 다른 통제다.
2. **env** — child env에 `CLAUDE_CODE_SIMPLE`이 없음을 확인한다. 이 변수만으로도 `--bare` 없이 bare mode가 켜진다.
3. **version** — pinned version의 `-p` 기본 모드가 bare인지 검사한다. bare가 기본인 version은 argv·env를 아무리 정제해도 구독 로그인을 쓸 수 없으므로, allowlist 통과 여부와 무관하게 `BareModeDefault`로 기동을 거부한다.

3번이 필요한 이유는 이 값이 고정이 아니기 때문이다. `--bare`는 현재 opt-in이지만 공식 문서는 향후 release에서 `-p`의 기본값이 된다고 예고하고 있다. 기본값이 바뀌는 release가 allowlist에 들어오면 §5의 다른 검사는 모두 통과하면서 실행만 API key를 요구하게 된다. version 검사를 auth 검사와 분리해 두는 것이 이 실패를 fail-open이 아니라 fail-closed로 만든다.

3번의 판정 근거는 문서 문장이 아니라 pinned version 실행 결과여야 한다. API key가 없는 정제 child env에서 해당 version의 `-p` 최소 turn이 구독 인증으로 성립하는지를 conformance fixture로 확인하며, 이 fixture는 [gate 2 usage attribution smoke test](../smoke/gate-2-usage-attribution.md)의 T-2에서 같은 절차로 만들어진다.

> 실측 기준선 (2026-08-22, Claude Code 2.1.239): bare mode는 `CLAUDE_CODE_SIMPLE` 환경변수 또는 argv의 `--bare`로만 켜지며, `-p`가 이를 암묵적으로 켜는 경로는 없다. 이 version에서는 3번 검사가 통과한다. 이후 version은 같은 방식으로 다시 확인한다.

### 과금 귀속 한계

auth preflight는 잘못된 credential 선택을 막는 로컬 안전 gate이지 provider 청구 원장의 최종 증거가 아니다. 실제 사용량 귀속은 별도 smoke test로 확인하고, 확인 전 README에 “구독 과금 보장”을 쓰지 않는다. 해당 test의 절차·합격 기준·오독 함정은 [gate 2 usage attribution smoke test](../smoke/gate-2-usage-attribution.md)에 있다.

## 6. process lifecycle과 stream 경계

- `asyncio.create_subprocess_exec`처럼 `shell=False`·분리 argv를 보장하는 API만 사용한다.
- stdout과 stderr는 서로 독립된 task가 동시에 읽는다.
- stdout JSONL 한 줄의 v0 상한은 1 MiB다. 초과, malformed JSON, truncated UTF-8, schema 불일치는 turn failure다.
- stderr는 memory의 64 KiB ring buffer까지만 유지하며 allowlist diagnostic code로 변환한다. raw 내용은 영속화하지 않는다.
- queue가 가득 차면 stream reader가 bounded backpressure를 적용하며 무한 memory 증가를 허용하지 않는다.
- 정상 cancel은 stdin close 또는 provider가 지원하는 graceful signal을 먼저 보낸다. 5초 뒤에도 살아 있으면 Windows process tree를 종료하고, 최대 10초 동안 `wait`한다.
- participant turn 시작 20분 뒤 broker timer가 cancel을 호출한다. timeout은 provider 응답이 계속 streaming 중이어도 연장하지 않는다.
- broker shutdown은 모든 active run에 cancel을 수행한 뒤 drain task와 process handle을 전수 확인한다.
- 종료 결과를 확정할 수 없으면 성공이나 재시도 가능으로 추정하지 않고 `unknown_outcome`으로 둔다. 같은 native session의 자동 resume·재실행은 reconciliation 전 금지한다.

고정 상한과 시간은 v0 conformance fixture의 일부다. 실제 측정으로 부적합함이 드러날 때 ADR과 test를 함께 바꾸며, 환경변수 설정으로 미리 일반화하지 않는다.

## 7. canonical event와 session·turn 상태

### event type — 정확히 8개

1. `session.started`
2. `turn.started`
3. `runtime.activity.started`
4. `runtime.activity.finished`
5. `message.committed`
6. `turn.completed`
7. `turn.failed`
8. `artifact.committed`

`session.started` payload는 2~8명의 immutable participant roster, 선택된 budget profile, seed·standby 상태, profile별 concurrency/delegation/round/size limit을 canonical order로 담는다. provider 고유 event는 adapter 내부에서 이 집합으로 정규화한다. native worker spawn·finish는 `runtime.activity.*` payload의 `activity_kind=native_worker`로 표현하며 별도 event type을 늘리지 않는다. 알 수 없는 provider event는 raw payload를 저장하지 않고 allowlist metadata를 포함한 diagnostic으로 처리한다. `unknown_outcome`은 event type이 아니라 run outcome 상태다.

### envelope

| 필드 | 의미 |
|---|---|
| `event_id` | SQLite가 부여하는 DB 전체 단조증가 integer PK |
| `session_id` | Whole Life 논리 협업 session |
| `session_seq` | session 내부 replay 순서; `(session_id, session_seq)` unique |
| `run_id` | 한 native process 실행. provider process 전의 `phase=pre_start` 실패에서는 null, 그 밖의 turn event에서는 필수 |
| `task_id` | 한 `whole-life run` 명령에서 broker가 만든 논리 업무 UUID. 해당 session의 모든 event에서 동일하고 null이 아님 |
| `participant_id` | immutable roster의 1급 협업 agent. broker가 만든 session/artifact event에서는 null, participant turn/activity/message event에서는 필수 |
| `round_id` | 같은 projection 기준으로 병렬 실행되는 turn 묶음 |
| `native_session_id` | provider session; 새 turn 시작 전에는 비어 있을 수 있음 |
| `event_type` | 위 8개 중 하나 |
| `occurred_at` | UTC ISO-8601 |
| `source` | `broker`, `codex`, `claude` 중 하나 |
| `payload_json` | schema 검증 뒤 canonical bytes로 만든 JSON text |
| `payload_sha256` | canonical UTF-8 bytes의 SHA-256 |

`runtime.activity.*`가 native worker를 나타낼 때 payload에는 `activity_kind`, `parent_participant_id`, provider가 공개한 경우의 `native_child_id`, `observability`, status만 허용한다. native worker의 raw prompt·reasoning·stderr는 Journal에 넣지 않는다.

### canonical JSON

- 허용 schema의 object·array·string·integer·boolean·null만 받는다. float와 NaN/Infinity는 v0 payload에서 거부한다.
- string은 Unicode NFC로 정규화한다.
- object key는 Unicode scalar value(code point) 오름차순으로 정렬하고 insignificant whitespace 없이 UTF-8, `ensure_ascii=false`로 직렬화한다.
- path는 payload hash 전에 Windows canonical absolute path와 별도 path policy 검사를 통과한다. path 비교는 case-insensitive이며 root containment를 component 단위로 확인한다.
- serializer는 `Journal` 안의 concrete 함수 하나만 사용한다.

### turn state machine

```text
session_created -> session_active -> dossier_committed

participant_turn_created -> queued -> running -> completed
                                   |         -> failed
                                   |         -> unknown_outcome
                                   -> failed
```

- clean user cancel은 active turn을 cancel·wait한 뒤 완료·실패·unknown 상태를 그대로 포함한 partial dossier를 commit해 `dossier_committed`로 끝낸다. user가 시작한 cancel은 cancel 전에 terminal result가 이미 commit된 turn을 제외하고 항상 `unknown_outcome`이다. broker crash는 `session_active`로 남고 startup에서 명시적 resume 또는 partial dossier export 전에는 terminal로 추정하지 않는다. v0에 별도 `session_abandoned` 상태·event는 두지 않는다.
- `session.started`는 roster가 양 provider를 포함하고 수량·profile 불변식을 만족할 때 한 번만 commit한다. 이후 roster·profile 변경은 v0에서 허용하지 않는다.
- 양 provider의 subscription auth·read-only·native delegation preflight가 모두 성공하기 전에는 `session.started`를 만들지 않는다.
- broker는 같은 round의 ready participant turn을 `participant_id` ordinal order로 queue에 넣고, active participant turn 4개 상한 안에서 실행한다.
- `turn.started`는 participant/native-session lock과 delegation budget을 얻고 projection hash를 확정한 뒤, provider process 시작 직전에 commit한다.
- Codex adapter는 provider의 terminal completed event와 process exit 0을 모두 확인해야 `turn.completed`를 만든다.
- Claude adapter는 terminal `result` stream event와 process exit 0을 모두 확인해야 `turn.completed`를 만든다.
- terminal provider event 없이 process exit 0만 관찰되면 성공으로 간주하지 않고 `unknown_outcome`이다.
- terminal failure event, nonzero exit, parse/size/schema violation은 `turn.failed`다.
- projection size·auth·delegation preflight처럼 provider process 시작 전 실패는 `turn.started` 없이 `turn.failed`로 끝날 수 있으며 payload에 `phase=pre_start`를 기록한다.
- process 종료 여부나 외부 effect 여부를 확정할 수 없는 crash/cancel은 `unknown_outcome` 상태로 남고 자동 재실행하지 않는다.
- terminal result가 먼저 commit됐으면 그 상태가 끝이다. terminal commit 전에 broker의 20분 hard timeout이 먼저 발생하면 이후 rate-limit event나 깨끗한 process 종료가 관찰돼도 turn 결과를 항상 `unknown_outcome`으로 둔다.
- timeout 전에 provider가 명시적 rate-limit terminal event를 내고 process 종료까지 확인되면 `turn.failed`에 allowlist diagnostic을 남긴다. terminal 상태나 종료를 확정할 수 없으면 `unknown_outcome`이다.
- 한 run에 `turn.completed`와 `turn.failed`가 동시에 존재할 수 없다.
- provider stream이 active native worker를 보고한 상태에서 terminal result가 오거나 timeout/cancel 뒤 lifecycle을 확정할 수 없으면 해당 turn은 `unknown_outcome`이다.

## 8. projection과 인식론적 권한

다른 participant·문서·artifact의 내용은 모두 다음 경계를 가진 untrusted data block으로 직렬화한다.

```text
BEGIN_UNTRUSTED_CONTEXT
source_session_id: ...
source_event_range: ...
source_participant_id: ...
projection_sha256: ...
content: ...
END_UNTRUSTED_CONTEXT
```

이 block 안의 명령문·승인 문구·role-like text는 instruction이 아니라 data다. system instruction과 사용자 요청 뒤의 별도 section으로만 전달한다.

- adapter가 제출할 수 있는 epistemic status는 `agent_claim`과 `needs_verification`뿐이다.
- agent 출력의 `verified_fact`, “사용자 승인”, 서명 흉내는 일반 text로 취급하고 권한을 주지 않는다.
- v0 runtime 중에는 claim을 `verified_fact`로 바꾸는 interactive promotion API를 두지 않는다.
- `verified_fact`는 session 시작 전에 사용자가 제공한 신뢰 입력 또는 broker의 결정론적 원문 대조 규칙이 provenance와 함께 만든 값만 허용한다.
- 향후 사용자 확인으로 runtime 중 승격하는 기능을 추가하려면 별도 canonical command/event와 ADR이 먼저 필요하다. 자연어 문장만으로 구현하지 않는다.

### 구조화 participant result와 handoff capsule

각 participant turn은 broker가 검증하는 하나의 JSON object를 최종 결과로 낸다. provider-side schema option이 있으면 같은 schema를 미리 전달하지만 최종 권한은 broker 검증이다.

```json
{
  "full_answer": "dossier에 보존할 원문 또는 비평",
  "handoff_capsule": {
    "summary": "다른 participant에게 전달할 압축 결론",
    "claims": [
      {
        "claim_id": "participant 내부 unique ID",
        "status": "agent_claim",
        "text": "주장",
        "evidence_refs": ["source reference"]
      }
    ],
    "disagreements": [
      {"target_claim_id": "상대 claim ID", "reason": "반대 근거"}
    ],
    "open_questions": ["남은 질문"],
    "confidence": "medium",
    "evidence_complete": false,
    "material_conflict": false,
    "needs_more_agents": false
  }
}
```

- 위 object의 모든 field는 필수이며 추가 field는 거부한다. 누락·타입 불일치는 `ResultSchemaViolation`이다.
- `claims`는 최대 8개, `disagreements`와 `open_questions`는 각각 최대 4개다. `confidence`는 `low|medium|high`만 허용한다.
- capsule 안의 claim status도 `agent_claim|needs_verification`만 허용한다. broker가 capsule을 사실·승인으로 승격하지 않는다.
- `evidence_complete=true`는 해당 participant가 task 결론에 material하다고 제출한 모든 claim에 적어도 하나의 `evidence_refs`가 있다는 구조 조건만 뜻한다. 근거의 진실성·충분성을 broker가 검증했다는 뜻은 아니다.
- `full_answer`는 dossier에 보존하지만 다른 participant prompt에는 절대 넣지 않는다. native worker raw output도 넣지 않는다.
- broker는 schema와 profile별 UTF-8 byte limit을 초과한 결과를 자르거나 model로 요약하지 않고 `ResultSchemaViolation` 또는 `ResultTooLarge`로 실패 처리한다.
- capsule은 participant가 자기 context에서 직접 만들며, capsule만 만들기 위한 별도 summarizer·judge model call은 없다.
- schema를 통과한 위 전체 object는 해당 turn의 `message.committed` payload다.

### token budget profile

아래 세 profile만 v0에 둔다. 숫자는 정확한 provider 토큰 예측이 아니라 broker가 통제할 수 있는 top-level turn·native worker·교환 bytes의 상한이다.

| profile | 처음 active | 추가 active | top-level turn 상한 | native worker 상한 | 원 답변 상한 | 비평·추가검토 상한 | capsule 상한 | handoff projection 상한 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `economy` | seed 2명 | 0명 | 4 | seed round 1에서 각 1개, session 최대 2개 | 12 KiB | 4 KiB | 4 KiB | 16 KiB |
| `balanced` 기본 | seed 2명 | standby 최대 2명 | 6 | 각 participant의 최초 활성화 turn에서 1개, session 최대 4개 | 24 KiB | 8 KiB | 8 KiB | 64 KiB |
| `deep` | roster 전원 | 0명 | `2 × roster 수` | round 1에서 participant당 총 3개·동시 3개 | 48 KiB | 16 KiB | 16 KiB | 128 KiB |

- KiB는 1024 UTF-8 bytes다. provider tokenizer가 다르므로 byte limit을 token 수라고 부르지 않는다.
- `economy`는 seed 두 명의 독립 원 답변과 상대 capsule 1개에 대한 짧은 비평까지만 한다.
- `balanced`는 seed 두 명이 round 1 원 답변, round 2 상호 capsule 비평을 한다. round 2 capsule 중 하나라도 `needs_more_agents=true`, `confidence=low`, `evidence_complete=false`, `material_conflict=true`이거나 seed round 2 turn이 `failed|unknown_outcome`이면 ordered standby 중 가능한 수만큼, 최대 2명을 round 3에 활성화한다. 새 model이 escalation을 판정하거나 의미를 재분류하지 않는다.
- `balanced` round 3 participant는 seed 두 명의 round 1·2 capsule만 canonical order로 받고 독립 추가검토를 한다. 여기서 “capsule만”은 성공 output의 출처 집합을 제한한다는 뜻이며, failed·unknown seed의 status·event placeholder는 round projection 공통 규칙에 따라 같은 slot에 넣는다. seed에게 round 3 결과를 다시 보내는 네 번째 round는 없다.
- `deep`은 roster 전체가 round 1 원 답변을 만든다. round 2에서 각 participant는 canonical cyclic order의 다음 **서로 다른** participant slot을 `min(3, roster 수 - 1)`개 고른다. 선택 slot이 failed·unknown이면 그 slot을 건너뛰어 다른 성공 participant로 채우지 않고 status·event placeholder를 넣는다. all-to-all full-mesh projection은 하지 않는다.
- profile 상한에 따라 실행되지 않은 standby participant는 실패가 아니라 `not_activated`이며 dossier에 profile과 이유를 남긴다.
- 모든 participant는 활성화되면 동일한 실행 권한을 가진다. standby는 열등한 agent 유형이 아니며, 사용자가 전원 실행을 원하면 session 시작 전에 `deep`을 선택한다.

### cache-friendly prompt와 model policy

- system instruction·result schema·tool allowlist·working directory·provider model은 같은 native session 동안 고정하고, 사용자 task와 untrusted capsule 같은 동적 내용은 prompt 끝에 둔다.
- broker는 user가 고른 read-only input만 snapshot에 넣고 prompt에는 canonical manifest(path·size·SHA-256)를 전달한다. 같은 source body를 participant 수만큼 prompt에 복사하지 않으며, participant에는 검색 후 필요한 범위만 읽으라는 정책을 준다.
- unrelated task는 이전 native session을 resume하지 않고 새 Whole Life session과 native session을 만든다. history를 줄이기 위한 별도 model 요약 turn은 만들지 않는다.
- MCP·plugin·tool 목록을 round 사이에 바꾸지 않는다. native worker budget 0은 고정 tool surface 안의 cooperative policy로 전달하고, 위반 활동이 관찰되면 cancel한다.
- v0는 model 이름과 subscription entitlement가 변한다는 이유로 자동 model router를 두지 않는다. user가 participant model을 명시하지 않으면 각 공식 CLI의 현재 기본 model을 사용하고 실제 model ID를 provenance에 기록한다.
- token profile은 provider model이나 effort를 자동 변경하지 않는다. model·effort 선택은 user가 명시한 값 또는 공식 CLI의 현재 기본값을 그대로 쓰고 provenance에 실제 보고값만 기록한다.
- provider의 automatic prompt cache를 Whole Life의 보장으로 주장하지 않는다. cache hit/miss는 provider가 보고한 경우에만 telemetry에 기록한다.

### round projection

- round 1에서는 해당 profile의 active participant가 같은 사용자 task와 자신의 role을 받고 서로의 output은 받지 않는다.
- round 2 이후에는 위 profile schedule이 선택한 상대의 검증된 `handoff_capsule`만 canonical participant order로 받는다.
- failed·unknown·not-activated participant의 빈자리는 조용히 제거하지 않고 status·event reference만 넣는다.
- handoff projection의 canonical UTF-8 bytes가 profile 상한을 넘으면 provider 한도를 추정해 밀어 넣거나 임의 요약·truncation하지 않고 `ProjectionTooLarge`로 해당 turn을 중단한다.

### usage ledger와 사용자 표시

- provider가 공식 event에서 보고한 경우에만 `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`를 `turn.completed|turn.failed` payload의 nullable usage object에 기록한다. 없는 값은 0이 아니라 `null`이다.
- subscription quota, 남은 plan usage, 청구금액은 위 token field에서 역산하지 않는다. provider가 출력한 USD 추정치도 subscription billing의 확정값으로 표시하지 않는다.
- provider 보고와 무관하게 top-level turn 수, native worker 관찰 수, prompt/capsule/result UTF-8 bytes, elapsed time은 broker가 기록한다.
- provider process를 하나라도 시작하기 전에 CLI에 profile, active/standby 수, 가능한 top-level turn 최대치, native worker 최대치, handoff byte 상한을 표시한다. 정확한 token·가격 예측으로 표현하지 않는다.
- 각 turn commit 뒤 현재 turn/worker 사용량과 provider-reported token을 갱신한다. token telemetry가 없으면 `unknown`으로 표시하고 bytes나 turn을 token으로 환산하지 않는다.
- token field는 새 event type을 만들지 않고 기존 terminal event에 포함한다. read model과 dossier usage summary는 이 event에서 결정론적으로 파생한다.

## 9. Journal

### 위치와 기동 gate

- DB: `%LOCALAPPDATA%\WholeLife\whole-life.db`
- artifact: `%LOCALAPPDATA%\WholeLife\artifacts`
- temp: `%LOCALAPPDATA%\WholeLife\tmp`
- 위 세 경로는 같은 local volume이어야 한다.
- UNC·mapped network volume, OneDrive 등 알려진 sync root, root 또는 ancestor의 junction/reparse point가 감지되면 시작을 거부한다.

### 쓰기 모델

- 한 `asyncio.Queue`가 write command를 받는다.
- 한 broker process의 한 `sqlite3` writer connection만 write한다.
- `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=FULL`을 확인하고 기대값이 아니면 기동 실패한다.
- event append와 current run/session state 갱신은 한 transaction이다.
- commit에 성공한 뒤에만 in-process subscriber에 fan-out한다.
- subscriber 실패는 이미 acknowledged된 transaction을 되돌리지 않는다. subscriber는 last `(session_id, session_seq)`에서 replay한다.
- 별도 event bus, projector service, repository interface, inbox/outbox는 없다.

### 복구 보장

RPO 0 표현은 **정상 local durable volume에서 SQLite가 acknowledged한 FULL-synchronous transaction 이후 process crash**에만 적용한다. 장치·volume 손실과 provider 외부 effect는 backup 또는 reconciliation 전까지 보장하지 않는다.

startup은 다음을 수행한다.

1. nonterminal run을 찾는다.
2. provider process 생존을 확정할 수 없으면 `unknown_outcome`으로 표시하고 자동 재실행을 금지한다.
3. artifact event가 가리키는 파일의 존재·hash를 확인한다. 불일치는 기동 차단 diagnostic이다.
4. artifact root의 unreferenced content-addressed file은 hash를 검증해 orphan inventory에 기록하되 자동 삭제·committed 승격하지 않는다.

## 10. ArtifactCommitter

runtime은 artifact root를 쓰지 못하고 broker만 다음 절차를 수행한다.

1. output root를 절대 canonical path로 해석하고 allowlisted app root 하위인지 component 단위로 확인한다.
2. root부터 target parent까지 각 component의 junction/reparse point를 거부한다.
3. content bytes의 SHA-256을 계산하고 `<sha256>.<extension>` final name을 만든다. extension은 exporter가 고정한 allowlist만 쓴다.
4. 같은 volume의 temp directory에 새 파일을 exclusive-create하고 bytes를 기록한 뒤 flush·`fsync`한다.
5. Windows에서 기존 destination을 교체하지 않는 same-volume rename을 수행한다.
6. destination이 이미 존재하면 기존 bytes의 hash를 다시 확인한다. 같으면 deduplication으로 반환하고, 다르면 hash invariant failure로 중단한다. overwrite는 하지 않는다.
7. final file을 hash-readback한 뒤 `artifact.committed` event를 commit한다.

file rename 뒤 DB commit 전 crash가 나면 orphan file만 남는다. startup inventory가 이를 보고하지만 자동 삭제하거나 성공 event로 승격하지 않는다. `artifact.committed`는 파일 존재·hash-readback 뒤에만 생기므로 정상 local volume의 process crash에서 dangling committed event를 만들지 않는다.

## 11. 결정론적 dossier

`DossierExporter`는 model을 호출하지 않는다. 하나의 논리 session event를 `session_seq` 오름차순으로 읽고 다음 section을 정확한 순서로 만든다.

1. 참여 agent roster, budget profile, active·standby 결과, usage summary
2. round 1 원 답변 — participant별
3. round 2 이후 상호비평·추가검토 — round·participant별
4. native delegation 관찰 요약 — participant별
5. 검증된 공통사실
6. 미해결 충돌
7. 사용자 결정 필요 항목
8. provenance

### byte 동일성 규칙

- UTF-8, BOM 없음, LF newline, 마지막 newline 1개
- heading text와 section order 고정
- section 2는 `session.started` roster 순서다. section 3은 `round_id` 오름차순 뒤 roster 순서이며, 같은 participant 내부 항목은 `(session_seq, message ordinal)` 오름차순이다. 따라서 balanced round 3 추가검토도 section 3에 결정론적으로 포함된다.
- 없는 section은 heading과 `없음` 한 줄을 남긴다.
- timestamp는 source event의 UTC 값을 그대로 쓰며 현재 시각을 새로 삽입하지 않는다.
- provenance에는 source event ID·session sequence·payload hash·native session ID를 고정 필드 순서로 쓴다.
- native worker는 provider가 공개한 lifecycle ID와 status만 provenance에 쓰며, 공개하지 않은 ID를 broker가 추정하지 않는다.
- usage summary는 provider-reported token field와 broker-observed turn·worker·byte field를 분리하고, 보고되지 않은 token은 `unknown`으로 쓴다. subscription quota·청구액으로 환산하지 않는다.
- 같은 committed event set과 exporter version이면 dossier bytes와 SHA-256이 같아야 한다.

## 12. conformance와 안전 gate

구현은 아래 실패 재현 test부터 만든다. test를 통과한 뒤 안전 불변식을 하나씩 고의로 제거하는 mutation으로 실제 검출 여부를 확인한다.

### 인증

- exact CLI version이 compiled-in tested allowlist에 없거나 version 출력 schema가 바뀌면 `UnsupportedCliVersion`으로 기동 거부
- child env에 API key/token 변수가 있으면 기동 거부
- 로그인·auth method·provider 경로·subscription 필드가 불명확하거나 schema가 바뀌면 기동 거부
- `apiProvider`가 `firstParty`가 아니면 기동 거부
- auth status 출력의 `email`·`orgId`·`orgName`이 Journal·artifact·log·진단 메시지에 남지 않음
- 최종 argv에 `--bare`가 들어가면 실행 전 기동 거부
- child env에 `CLAUDE_CODE_SIMPLE`이 있으면 기동 거부
- pinned version의 `-p` 기본 모드가 bare이면 다른 auth 검사가 모두 통과해도 `BareModeDefault`로 기동 거부
- `CODEX_HOME`이 명시되지 않았거나 auth 판정과 실행 process에서 서로 다르면 기동 거부
- status와 실제 process가 동일 env builder를 쓰는지 검사

### stream·process

- malformed JSONL, truncated UTF-8, 1 MiB 초과 line, unknown schema
- stderr 64 KiB 초과 폭주에서도 deadlock·무한 memory 증가 없음
- provider 중간 종료, terminal event 없는 exit 0, terminal success 뒤 nonzero exit
- terminal event 없는 exit 0은 `unknown_outcome`
- timeout·rate limit·broker shutdown 후 orphan process 0개
- 20분 hard timeout은 clean process 종료 여부와 무관하게 항상 `unknown_outcome`
- 명시적 rate-limit terminal+확정 exit는 `turn.failed`, 불명확한 종료는 `unknown_outcome`
- timeout이 rate-limit terminal commit보다 먼저 발생하면 `unknown_outcome`, terminal commit이 먼저면 기존 terminal 상태 유지
- user가 시작한 cancel은 이미 terminal commit된 turn 외에는 항상 `unknown_outcome`이고 partial dossier에 그대로 표시
- 동일 native session 동시 resume 거부
- cancellation 불명확 시 자동 재실행 없이 `unknown_outcome`
- 8 participant roster·participant turn 동시실행 4개·provider 혼합 queue의 결정론적 순서
- 같은 ordered roster 입력에서 participant ID·canonical order 동일, 중복 ID·한 provider만 있는 roster 거부
- profile별 seed·standby·round·turn·worker 상한을 넘는 run을 schedule하지 않음
- `economy`는 seed round 1 전체에서 native worker 최대 2개, `balanced`는 활성 participant 전체에서 최대 4개, `deep`은 round 1 participant당 총 3개·동시 3개 budget
- Codex participant별 native worker 동시 3개 hard cap과 Claude participant의 `cooperative` enforcement 상태 기록
- worker start 누계를 관찰할 수 없는 runtime은 profile budget을 지킨 것으로 간주하지 않고 preflight 실패
- profile의 turn delegation budget보다 하나 많은 worker start를 관찰하면 즉시 cancel·`unknown_outcome`
- Claude가 4번째 worker 활동을 시작한 것이 관찰되면 cancel·`unknown_outcome` 처리
- participant turn 20분 hard timeout 뒤 process tree와 drain task 0개
- native worker가 남은 상태에서 participant turn을 완료 처리하지 않음
- Claude `Agent` worker와 Codex subagent가 parent read-only 권한을 넘어설 수 없음
- Claude subagent와 Codex subagent의 tool output·conversation이 parent prompt에 raw transcript로 합쳐지지 않고 provider가 공개한 result/summary 경계로만 돌아옴
- input의 `AGENTS.md`·CLAUDE.md·plugin·hook·MCP 설정이 runtime control instruction으로 로드되지 않음
- Claude depth 2 시도가 관찰되면 broker가 즉시 cancel·`unknown_outcome` 처리한다. provider가 hard로 거부해 줄 것으로 기대하지 않는다 — 2.1.240 실측에서 거부되지 않았다. Codex nested spawn도 관찰되면 같은 처리
- native delegation capability 미지원 시 silent downgrade가 아니라 `DelegationUnsupported`

### Journal·artifact

- duplicate event/write command 재주입에도 unique sequence와 artifact 중복 없음
- SQLite commit 직후 강제종료 후 replay state hash 일치
- OneDrive·UNC/network·reparse DB root 기동 거부
- output traversal, case variant, junction/reparse, 기존 file overwrite 차단
- rename 뒤 event 전 crash의 orphan inventory, committed event의 file/hash 불일치 기동 차단

### 권한·dossier

- agent가 만든 `verified_fact`, 승인 문장, prompt injection이 권한으로 승격되지 않음
- untrusted context의 role/instruction text가 data block 밖으로 탈출하지 않음
- participant A의 native worker raw output이 participant B projection으로 직접 유출되지 않음
- participant A의 `full_answer`가 participant B projection으로 들어가지 않고 schema-valid capsule만 전달됨
- round 2 이후 projection이 profile의 deterministic fanout과 roster order를 지키며 failed·not-activated status를 보존함
- projection 한도 초과 시 silent truncation·model 요약 없이 실패함
- result·capsule의 schema·개수·UTF-8 byte 상한 초과 시 silent truncation 없이 실패함
- capsule 필수 field 하나씩 누락하거나 추가 field를 넣으면 `ResultSchemaViolation`
- `balanced`는 round 2 escalation field가 모두 해제되면 standby를 실행하지 않고, 하나라도 trigger이면 ordered standby 최대 2명만 실행함
- `balanced` seed round 2가 failed·unknown이면 가능한 ordered standby 최대 2명을 실행함
- `deep` 8명에서도 participant마다 상대 capsule 최대 3개만 받고 full-mesh 원문 교환이 생기지 않음
- 동일 event log·profile에서 activation plan과 dossier가 동일함
- provider token telemetry 누락은 `unknown`으로 남고 0·비용·quota로 오인되지 않음
- model/tool/cwd/static prompt prefix가 round 사이에 변하지 않으며 dynamic capsule은 suffix에만 추가됨
- 동일 event fixture를 여러 번 export해 byte·hash 동일
- 원 답변·비평·충돌이 빈 section으로 조용히 소실되지 않음

### 성능

100개 broker commit event에서 adapter 수신 timestamp부터 commit 후 subscriber delivery timestamp까지 p95 1초 이내. 이 지표는 AI 응답시간이나 turn 간 context 전달시간을 뜻하지 않는다.

## 13. finding closure matrix

| ID | 닫는 규범 | 상태 |
|---|---|---|
| F-1 Tool Gateway 허위 보장 | §2 비목표, §4 read-only | 닫힘 |
| F-2 auth fail-open | §5 | 닫힘 |
| F-3 storage/path/atomic/recovery | §9~10 | 닫힘 |
| F-4 이중 규범 아키텍처 | §1~2 | 닫힘 |
| F-5 agent direct write | §4, §10 | 닫힘 |
| F-6 dossier 비결정성 | §11 | 닫힘 |
| F-7 shallow adapter | §3~4 | 닫힘 |
| F-8 Windows subprocess/backpressure | §6 | 닫힘 |
| F-9 context receipt 과장 | §2, §8 | 닫힘 |
| F-10 event/sequence/hash 충돌 | §7, §9 | 닫힘 |
| F-11 epistemic 권한 | §8 | 닫힘 |
| F-12 turn boundary·측정 구간 | §2, §7, §12 | 닫힘 |
| F-13 participant 수가 Claude 1+Codex 1로 고정 | §2~4, §7~8 | 닫힘 |
| F-14 participant의 자율 subagent 실행력 없음 | §4, §6~8, §12 | 닫힘 |
| F-15 roster·round·native worker의 token 증폭 | §2, §4, §7~8, §11~12 | 닫힘 |
| F-16 CLI bare mode 기본값 전환 시 구독 전제 붕괴 | §4 transport, §5 bare mode gate, §12 인증 | 닫힘 |

## 14. 설계·저장소 gate

### private repository 생성 전

- 조사본 hash가 기준선과 일치하고 원문이 수정되지 않음
- Codex·Claude 양쪽의 네 스킬 사용 증거가 vault에 있음
- Claude 독립감사·비교감사·이 사양 최종감사가 동일 추적 session에 있음
- Claude 최종감사 P0·P1 미해결 0건
- P2는 연기 이유·도입 조건이 기록됨
- repository 첫 commit은 이 사양·ADR·CONTEXT·README·LICENSE·SECURITY·CONTRIBUTING·`.gitignore`만 포함
- staged secret scan과 경로 검사가 성공

### public 전환 전 — v0 구현과 별개의 외부 gate

- Anthropic·OpenAI 최신 공식 문서 또는 서면 답변으로 각 사용자의 자기 PC·자기 구독 CLI 실행 도구 허용 범위 재확인
- 두 CLI의 실제 usage 귀속 smoke test 통과
- README가 기술 가능성과 정책 허용을 구분하고 subscription 지원을 과장하지 않음
- credential·session token·사용자 설정·runtime DB/log가 Git history에 없음

public gate가 미확인인 것은 private local 설계의 P0/P1 blocker가 아니지만, public 전환은 fail-closed한다.

## 15. v0 이후 seam 도입 조건

- storage interface: 두 번째 실제 storage implementation이 생길 때
- web UI·AG-UI: browser에서 관찰·승인해야 하는 검증된 사용자 요구가 생길 때
- A2A/MCP orchestration: 제3 provider 또는 remote agent interoperability가 실제로 필요할 때
- Claude agent teams: stable로 승격되고 non-interactive resume·shutdown·event 관찰 계약이 conformance를 통과할 때
- recursive native delegation: 양 provider가 동일한 depth·권한 상속·cancel semantics를 공식 지원하고 runaway test를 통과할 때
- worktree write: read-only, duplicate run, crash recovery test를 통과하고 사용자가 agent direct edit의 별도 위험을 승인할 때
- distributed bus/outbox/lease: 두 번째 process writer 또는 remote node가 생길 때
- cryptographic event signing: remote/multi-user actor 위협모델과 key lifecycle이 정해질 때

## 관련 기록

- [[2026-08-20_AI실시간협업_아키텍처_기초설계]]
- [[2026-08-20_Whole-Life_아키텍처_정밀감사_Codex]]
- [[2026-08-20_Whole-Life_아키텍처_교차검증_Claude]]
- [[2026-08-20_Whole-Life_로컬구독형_v0_결정]]

## 공식 동작 근거

- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive/)
- [Using Codex with a ChatGPT plan and usage limits](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [Claude Code programmatic usage](https://code.claude.com/docs/en/headless)
- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Claude Code parallel agents](https://code.claude.com/docs/en/agents)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code costs and token usage](https://code.claude.com/docs/en/costs)
- [Claude Code prompt caching](https://code.claude.com/docs/en/prompt-caching)
- [Claude Agent SDK cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
- [Claude Code model and effort configuration](https://code.claude.com/docs/en/model-config)
- [SQLite Write-Ahead Logging](https://sqlite.org/wal.html)
