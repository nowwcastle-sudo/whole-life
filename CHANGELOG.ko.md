# 변경 기록

*[English](CHANGELOG.md) · 한국어*

이 저장소의 주목할 변경을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르고,
버전을 매길 것이 생기면 [유의적 버전](https://semver.org/lang/ko/)을 따른다.

**아직 아무것도 릴리스되지 않았다.** 설치 가능한 애플리케이션도, 실행되는 broker도 없다
— [시작하기](README.ko.md#시작하기) 참조. 지금 있는 것은 승인된 설계와 그 아래에 만들고 있는
런타임 이음매뿐이라, v0가 나올 때까지 모든 항목은 `Unreleased` 아래에 둔다.

각 항목은 **어떤 파일이 움직였는지가 아니라 시스템이 이제 무엇을 하고 무엇을 거부하는지**를 적는다.
인수 조건은 링크된 이슈에, 근거는 병합 커밋에 있다.

## [Unreleased]

### 추가

- **「신뢰되지 않는 디렉터리를 거부한다」를 그 거부가 실제로 관측됐을 때에만 적는 conformance
  증거, 그리고 시간 경계를 넘길 수 없는 probe** (#62).
  Codex 수집기는 0이 아닌 종료 코드 하나로 「거부한다」를 결론냈다: 첫 stderr 줄은 문서에
  렌더링되면서도 검사되지는 않아서, CLI 플래그가 바뀌었거나 설치가 깨졌어도 같은 문장이
  증거로 커밋될 수 있었다. 이제 판정은 식별된다 — 0이 아닌 종료는 관측된 첫 stderr 줄이
  고정된 trusted-directory 거부문일 때에만 거부로 세고, 그 밖의 실패는 결론을 커밋하는 대신
  수집을 멈추며, 수용하는 빌드는 여전히 기록할 수 있다. probe의 문서는 프롬프트를 보내지
  않는다고 주장하면서 코드는 보내고 있었다; 이제 docstring이 사실을 말하고, 실행은 Claude 쪽
  bare probe처럼 시간 경계 아래에서 돈다 — 경계를 넘기면 프로세스 트리 전체를 종료하고 수집을
  멈추므로, probe가 끝없는 대기가 되거나 말끔한 보고 뒤에 모델 요청을 살려 둘 수 없다.
- **launch decision이 child가 실제로 돈 디렉터리를 기록한다** (#63).
  working directory가 실제 spawn의 일부가 된 뒤로(#61), provider·실행 파일·인자가 같고 입력
  스냅숏 루트만 다른 두 run이 **똑같은 journal 기록**을 남겼다 — 감사자가 child가 어떤 데이터를
  읽을 수 있었는지 가릴 수 없었다. 이제 기록되는 decision은 spawn이 실제로 쓴 디렉터리를 다른
  모든 필드와 같은 방식으로 spawn된 plan에서 파생해 담으므로, 「시작된 것을 정확히 기록한다」는
  decision 객체의 약속이 지켜진다. plan의 미정 상태는 기록되지 않는다 — 미정이거나 절대 경로가
  아닌 디렉터리는 spawn 전에 거부되고, journal은 시작만 담는다.
- **절대 경로가 아닌 working directory는 child가 생기기 전에 거부한다** (#61).
  상대 경로는 운영체제가 spawn 시점의 **Broker 자신의 현재 디렉터리**에 대고 해석하므로, 받아들이면
  child가 어디서 도는지를 plan이 아니라 Broker의 실행 위치가 정하게 된다. Windows에서는 평범한
  「상대」보다 넓게 거부한다 — `/Windows` 같은 드라이브 없는 루트 경로와 `C:foo` 같은
  드라이브-상대 경로도 현재 드라이브·디렉터리에 대고 해석되므로 둘 다 거부된다. 존재하지 않는
  디렉터리도 같은 경계에서 거부돼, spawn 자체의 운영체제 오류가 아니라 그 경계의 다른 결정들과
  같은 pre-start 거부로 도착한다.
- **`close`가 말끔한 종료를 주장하는 대신 거두지 못한 것을 보고한다** (#46).
  fallback 직접 킬이 대기를 넘겼을 때 그 결과는 버려졌다: 살아남은 자식이 깨끗한 close와
  같은 모양으로 돌아와, 프로세스가 아직 돌고 있을 수 있는데도 「남은 것 없음」이
  주장됐다. 이제 `close()`는 `CloseReport`를 반환한다 — 남아 있는 child process 수와
  drain task 수, 코드가 관측한 사유 목록(killer 부재와 직접 킬 미거둠은 별개 항목) —
  그리고 0은 실제로 0이 확인됐을 때만 보고한다.
- **Codex 자식은 broker가 어쩌다 있던 곳이 아니라 plan이 고른 디렉터리에서 돈다** (#33).
  Codex child process는 broker 자신의 현재 디렉터리를 물려받았고, 고정된 CLI는 그곳이
  신뢰된 workspace가 아니면 거부한다 — 즉 turn이 시작될 수 있는지를 plan이 아니라
  broker의 실행 위치가 정하고 있었다. 이제 plan의 working directory가 양쪽 adapter의
  필수 인자로 배선되고, spawn 직전에 검사되며 — 미정이거나 없는 디렉터리는 아무것도
  시작되기 전에 거부된다 — 자식에게 명시적으로 전달된다. 중립 디렉터리가 저장소가
  아닌 Codex 쪽에서는 git 저장소 검사를 건너뛴다.
- **정리 실패가 정리를 요청한 취소를 덮지 않는다** (#34).
  `taskkill`로의 escalation이 실패하면 그 예외가 취소 자신의 답 자리에 올라와, 자기가
  멈추라고 한 run에 대해 호출자가 broker 환경에 대한 진단을 받았다. 이제 cancel은
  `UNKNOWN`을 답한다 — 그 말이 정확히 「트리가 죽었다고 확인하지 못했다」이다 — 그리고
  정리 실패는 turn이 아니라 broker에 대한 사실로 따로 기록한다. spawn 경로에서는 원래
  예외가 타입과 자리를 그대로 지키고 정리 실패는 note로 함께 실리며, fallback 킬은
  죽인 것을 거두기까지 하므로 자기가 끝낸 자식이 close에서 살아 있는 것으로 세어지지
  않는다.
- **정직하게 보고되고 broker가 지키는 native worker 상한** (#17).
  이제 각 provider가 동시 실행·총 start·위임 depth를 각각 `hard`·`cooperative`·`unsupported`로
  나눠 보고하고, **측정된 대로** 보고한다. Claude Code 2.1.240은 worker의 시작과 끝을 provider가
  발급한 식별자와 spawn depth까지 붙여 알리므로 세 상한이 관측된다 — 그리고 어느 것도 provider가
  거부하지 않는다. depth도 그렇다. 사양은 그것을 `hard`로 적어 두었지만, 녹화된 turn에서 worker가
  두 단계 깊이로 돌았고 아무것도 거부되지 않았다. Codex는 세 축 모두 `unsupported`다. 위임 측정이
  아직 실행되지 않았기 때문이다 — 라이브 시도가 구독 사용량 한도에 걸려 모델이 한 번도 돌지 않아,
  그 스트림이 worker에 대해 무엇을 알리는지는 **없다고 아는 것이 아니라 모르는 것**이다. 그런 상태의
  runtime은 조용히 single-agent turn으로 낮아지지 않는다 — 시작하지 않는다.
  worker start는 turn의 budget에 대고 세며, 한도를 넘는 첫 start가 turn을 취소하고
  `unknown_outcome`으로 둔다. 너무 깊은 worker도 같다. 그리고 자기가 띄운 worker의 끝이 알려지지
  않은 turn은 완료로 적지 않는다. plan이 자기 capability에 대해 하는 말은 근거가 아니다 —
  pre-spawn 게이트가 측정 표에서 행을 찾아 **전체를 대조**하므로, 지어낸 행도 축이 빠진 행도 빈 행도
  전부 거부된다.
- **런타임 계약과, 프로세스가 생기기 직전에 지나는 안전 게이트** (#11).
  사양 §4의 `AgentRuntime` 프로토콜을 런타임에 실제로 검사하므로, 연산 하나를 조용히 빠뜨린
  참여자는 no-op으로 넘어가지 않고 거부된다. 조립된 실행 계획은 프로세스가 되기 전에 반드시
  경계 하나를 지난다.
- **Claude Code·Codex CLI 구독 인증 preflight** (#12).
  broker는 각 CLI에게 「이미 로그인돼 있나」만 묻는다. credential을 읽거나 복사하거나 저장하지
  않는다. child 프로세스 환경은 부모 환경을 물려받는 대신 inherit allowlist로 새로 만들고,
  금지 변수 5개는 들어오면 거부한다.
- **Claude Code bare mode 게이트 — 최종 spawn 직전에 강제** (#13).
  `--bare`는 Claude Code가 인증을 어디서 가져오는지를 바꾸므로, 켜지는 순간 v0의 구독 전제가
  무너진다. argv · 최종 child 환경 · 실측한 `bare_default` 셋이 **독립된 통제**로 모두 일치해야
  턴이 시작된다.
- **fail-closed 경계를 지나서 시작하는 provider 턴** (#14).
  실행 파일은 직접 실행 가능한 PE 이미지로만 해석되고, 프롬프트는 UTF-8 stdin에만 실려 argv로
  가지 않는다. 읽기 전용 argv는 신규·재개 양쪽에서 유지되며, 같은 native session에 대한 두 번째
  동시 시작은 spawn 전에 거부된다. Windows `.cmd` 런처는 거부한다 — 명령 처리기 재파싱이 승인된
  분리 argv 경계를 깨뜨리는 것을 실측했기 때문이다.
- **경계 안에서 bounded로 관측하는 스트림과 두 증인으로 확정하는 종료 결과** (#15).
  `stdout`과 `stderr`를 한도 안에서 동시에 drain하고, 공식 provider 형태만 canonical event로
  정규화한다. 스키마 위반 · 잘못된 UTF-8 · 한도 초과 줄 · terminal 불일치 · 소비자의 조기 중단은
  전부 fail-closed로 처리하고 child·reader·transport를 남기지 않는다.
- **run의 프로세스 트리까지 끝내는 cancel·턴 타임아웃·close** (#16).
  cancel은 stdin을 닫고 기다린 뒤 Windows 프로세스 **트리**를 종료한다. 띄운 프로세스 하나만
  죽이면 CLI가 만든 worker가 살아남아, 아무도 기다리지 않는 턴이 계속 과금된다. killer는 `PATH`가
  아니라 `SYSTEMROOT` 아래에서 찾는다. cancel과 terminal 이벤트는 **먼저 온 쪽이 이기고**, 하드
  턴 타임아웃은 턴 시작에 고정돼 끊임없이 streaming하는 run이 영원히 미루지 못한다.

### 변경

- Python CI가 Windows 러너에서 돈다 (#14 후속). 이 프로젝트가 지키는 안전 불변식은 Windows
  동작이라, Linux에서 검증하면 다른 것을 증명하게 된다.

### 수정

- **끝까지 도는 실제 provider 턴** (#32). 진짜 Claude Code 턴이 매번 쓰는 줄 두 종류 —
  top-level 사용량 한도 통지와 `system` thinking token 추정치 — 가 stream 허용목록에 없어서
  첫 미인식 줄에서 run이 끝났다. 위임과는 아무 상관이 없었다. 인사 한 줄만 하는 턴도 한도 통지에서
  죽었으니, **실제 턴이 한 번도 완주한 적이 없었다.** 이제 둘 다 인식하고 넘긴다. 어느 쪽도 canonical
  event 여덟 개에 들지 않고 Journal에 닿지 않으며, 그 줄이 담은 것 — 한도 수치·리셋 시각·토큰
  추정치 — 은 정규화 결과에 남지 않는다. 근거는 누가 기억으로 쓴 fixture가 아니라 **녹화한 실제 턴을
  스위트에서 재생한 것**이다. 손으로 쓴 fixture에는 그것을 쓴 사람이 이미 알던 줄만 들어 있고, 그것이
  바로 초록 스위트와 턴을 완주하지 못하는 adapter가 공존한 이유다. 미지의 type과 미지의 `system`
  subtype은 **여전히 fail-closed** 다 — 허용목록을 넓혔지 없애지 않았다.

- preflight·launch의 경계 결함 네 건. 전부 합성 입력으로 재현한 뒤 고쳤다 (#12 후속).
  최종 게이트가 호출자가 만든 `allowlisted` 플래그를 믿고 있었는데 그것은 근거가 아니라 주장이라,
  이제 레코드 전체를 정본 지원 버전 표와 대조한다. 금지 변수 검사가 이름을 대소문자 구분해
  비교하는 바람에 환경변수 이름을 대소문자 없이 취급하는 Windows에서 `openai_api_key`가 child에
  그대로 전달됐다.
- **실패해도 아무것도 살려 두지 않는 프롬프트 전달** (#28). spawner는 자식을 먼저 만들고 나서
  프롬프트를 쓰기 때문에, 이미 읽기를 그만둔 자식에게 쓰면 그 쓰기가 예외를 던졌다. 아무도 잡지
  않아 핸들이 호출자에게 가지 못했고, 주인 없는 프로세스가 남았다. 읽는 쪽이 사라진 것은 이제
  transport 오류가 아니라 평범한 provider 결과로 다뤄져 자식의 exit code와 stderr로 판정되고,
  자식이 생긴 뒤의 그 밖의 실패는 예외가 나가기 전에 프로세스 **트리**를 끝낸다. 잘못된 플래그나
  인증 실패로 즉시 죽는 provider 바이너리가 정확히 이 경로다.

### 문서

- v0 사양과 ADR 0001은 [`docs/project-context.md`](docs/project-context.md)에 SHA-256으로 고정돼
  있고, 고정된 문서가 해시 갱신 없이 바뀌면 CI가 실패한다.
- [`PRODUCT.md`](PRODUCT.md)에 GUI 콘솔 제품 정의와 표면 설계를 기록했다.
- [`CONTEXT.ko.md`](CONTEXT.ko.md)는 도메인 용어집, [`CONTRIBUTING.ko.md`](CONTRIBUTING.ko.md)는
  작업 규약이다.
