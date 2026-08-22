# Whole Life

*[English](CONTEXT.md) · 한국어*

Whole Life의 도메인 용어집이다. Whole Life는 여러 공식 공급자 CLI를 한 대의 기기에서 돌리고, 그들의 서로 다른 답변을 **하나의 결정론적 기록**으로 만드는 로컬 broker다. 여기 실린 말은 [v0 명세](docs/spec/whole-life-v0.md)가 규범적으로 쓰는 용어다. 이슈 제목·테스트 이름·제안서를 쓸 때 이 말을 쓴다.

프로젝트 상태, 승인 베이스라인, v0 불변식, 공개 배포 관문은 **여기 없다** — [`docs/project-context.md`](docs/project-context.md)에 있다.

## 용어

### 프로세스

**Broker**:
일정 관리·세션 상태·모든 산출물 쓰기를 독점하는 **단일 로컬 Python 프로세스**. 다른 무엇도 쓰지 않는다.
_쓰지 않는 말_: 서버, 오케스트레이터, 데몬, 컨트롤러

**Runtime adapter**:
공급자 CLI 하나를 구동하는 이음매. v0에는 **정확히 둘** — Claude Code, Codex CLI.
_쓰지 않는 말_: 드라이버, 커넥터, 플러그인, 백엔드

**Preflight**:
공급자 프로세스를 띄우기 전에 **정제된 자식 환경에서** 돌리는 검사 — 인증·argv·CLI 버전. 실패하면 기동을 거부한다.
_쓰지 않는 말_: 헬스체크, 유효성 검사, 워밍업

**Bare mode gate**:
Claude Code가 API 키로 인증하고 있지 않음을 확인하는 preflight 검사. 최종 argv에 `--bare` 없음, 자식 환경에 `CLAUDE_CODE_SIMPLE` 없음, 고정 버전의 `-p` 기본값이 bare가 아님 — 셋을 확인한다.
_쓰지 않는 말_: API 키 검사, 인증 가드

### 일하는 주체

**Participant**:
세션의 **불변 roster**에 속한 1급 협업 에이전트. `participant_id`를 갖는다. 세션당 2~8명.
_쓰지 않는 말_: 에이전트(native worker도 에이전트다), 모델, 좌석, 멤버

**Native worker**:
participant가 **범위가 제한된 읽기 전용 작업**을 위임하는 공급자 네이티브 서브에이전트. participant가 아니며 roster에 절대 오르지 않는다.
_쓰지 않는 말_: 서브에이전트(공급자마다 뜻이 달라 모호함), 자식 에이전트, 헬퍼

**Roster**:
한 세션의 고정된 participant 집합. 세션이 시작되면 바뀌지 않는다.
_쓰지 않는 말_: 팀, 풀, 라인업

### 작업 단위

**Session**:
Whole Life의 논리적 협업 하나. `session_id`를 갖고, 여러 turn과 round에 걸친다.
_쓰지 않는 말_: 대화, 스레드, 잡

**Native session**:
공급자 자신의 세션. `native_session_id`를 갖는다. Whole Life session과 다른 것이며, 하나의 Whole Life session이 여러 개를 구동한다.
_쓰지 않는 말_: 공급자 스레드, 업스트림 세션

**Turn**:
세션 안에서 participant 하나가 실행되는 것.
_쓰지 않는 말_: 스텝, 이터레이션, 호출

**Round**:
같은 projection을 기준으로 **병렬 실행되는** turn 묶음. `round_id`를 갖는다.
_쓰지 않는 말_: 배치, 웨이브, 사이클

**Run**:
네이티브 프로세스 실행 하나. `run_id`를 갖는다. 공급자 프로세스가 뜨기 전에 실패하면 null이다.
_쓰지 않는 말_: 인보케이션, 실행, 프로세스

**Task**:
`whole-life run` 명령 하나가 만드는 논리 업무 단위. `task_id`를 가지며 그 세션의 **모든 event에서 동일**하다.
_쓰지 않는 말_: 잡, 요청, 티켓

### 상태와 산출물

**Journal**:
명령 큐 하나와 **쓰기 연결 하나**를 갖는 추가 전용 SQLite 이벤트 로그. 세션 상태는 여기서 **재생**되지, 따로 저장되지 않는다.
_쓰지 않는 말_: 로그, 데이터베이스, 이벤트 스토어, 히스토리

**Event**:
journal의 레코드 하나. **정확히 8종**이며, 그 밖의 것은 명세 변경이다.
_쓰지 않는 말_: 메시지, 엔트리, 레코드

**Projection**:
한 participant에게 **다른 participant들의 작업 중 보여주는 것**. profile이 크기를 제한하며, 절대 전체 답변이 아니다.
_쓰지 않는 말_: 컨텍스트, 프롬프트 컨텍스트, 뷰

**Handoff capsule**:
공급자 경계를 넘는, 크기가 제한되고 스키마 검증을 통과한 객체. **경계를 넘는 유일한 것** — 전체 답변은 다른 participant의 프롬프트에 들어가지 않는다.
_쓰지 않는 말_: 컨텍스트, 다이제스트, 스니펫, 요약

**Dossier**:
세션의 결정론적 내보내기. 원 답변·비평·합의·충돌·출처를 보존한다. 같은 커밋 이벤트 집합과 같은 exporter 버전이면 **바이트와 SHA-256이 동일**하다.
_쓰지 않는 말_: 리포트, 전사본, 요약, 출력

### 한도

**Profile**:
세션의 한도를 정하는 이름 붙은 예산 — `economy` · `balanced` · `deep`. turn 수·native worker 수·바이트 크기를 제한한다. **구독 쿼터나 과금을 예측하지 않는다.**
_쓰지 않는 말_: 모드, 티어, 프리셋, 플랜
