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

- preflight·launch의 경계 결함 네 건. 전부 합성 입력으로 재현한 뒤 고쳤다 (#12 후속).
  최종 게이트가 호출자가 만든 `allowlisted` 플래그를 믿고 있었는데 그것은 근거가 아니라 주장이라,
  이제 레코드 전체를 정본 지원 버전 표와 대조한다. 금지 변수 검사가 이름을 대소문자 구분해
  비교하는 바람에 환경변수 이름을 대소문자 없이 취급하는 Windows에서 `openai_api_key`가 child에
  그대로 전달됐다.

### 문서

- v0 사양과 ADR 0001은 [`docs/project-context.md`](docs/project-context.md)에 SHA-256으로 고정돼
  있고, 고정된 문서가 해시 갱신 없이 바뀌면 CI가 실패한다.
- [`PRODUCT.md`](PRODUCT.md)에 GUI 콘솔 제품 정의와 표면 설계를 기록했다.
- [`CONTEXT.ko.md`](CONTEXT.ko.md)는 도메인 용어집, [`CONTRIBUTING.ko.md`](CONTRIBUTING.ko.md)는
  작업 규약이다.
