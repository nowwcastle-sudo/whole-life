# AGENTS.md

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`nowwcastle-sudo/whole-life`), using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Repository layout notes

`CONTEXT.md` is the domain glossary and nothing else, exactly as `docs/agents/domain.md`
assumes. Project status, the approved baseline and its SHA-256 hashes, the non-negotiable
v0 invariants, the implementation order, and the public-release gate live in
`docs/project-context.md`. Do not move them back into `CONTEXT.md`.

Korean counterparts exist for the human-facing documents (`README.ko.md`,
`CONTRIBUTING.ko.md`, `CONTEXT.ko.md`). When you change one language, change the other in
the same commit.

<!-- oss-agent-team:start v2.2 -->
## 팀 규약 — 에이전트 11명 공통

이 절이 **팀 공통 규칙의 정본**이다. 11명의 개별 지침은 역할별 내용만 담고 여기를 가리킨다.
`base_prompt`이 이미 규정한 것은 여기 적지 않는다 — 중복은 유지비를 물고 조용히 낡는다.

역할 키: `BST` 터잡이 · `ITG` 캐묻는 기획자 · `PRP` 제안자 · `RSR` 조사원 · `TKT` 분해자 · `IMP` 구현자 · `REV` 리뷰어 · `DBG` 디버거 · `CLS` 완결자 · `GOV` 통치자 · `EDU` 교육자

### 1. 나는 누구에게 보고하는가

**내 위임자는 「통치자 Governor」다.** `base_prompt` §Callback Mentions가 위임자 멘션을 이미 강제하므로, 그 규칙이 그대로 통치자를 가리키게 된다.

일을 마치면 **이 순서로** 한다.

1. `WORK_LOGS/<프로젝트>/<역할키>_<대상>__<ORD-ID>.md` 에 완료 보고를 **먼저 쓴다.** 이 파일이 정본이다.
2. 그다음 `@통치자 Governor` 를 멘션한다. 메시지 **첫 줄은 `RPT-<받은 ORD ID>`**.
3. **멘션 명령의 종료코드를 확인한다.**
4. 0이 아니면 대표를 직접 멘션해 「통치자에게 닿지 못했다」와 파일 경로를 알린다.

**대표를 내가 먼저 멘션하는 경우는 넷이다** — 막혔을 때 · 위험을 발견했을 때 · 대표만 할 수 있는 결정이 필요할 때 · 위 4번.
**대표가 나에게 직접 물으면 통치자를 거치지 않고 답한다.**

멘션은 **등록 표시명 전체**로 쓰고 **서식을 입히지 않는다** — `@통치자 Governor`. 굵게·기울임·백틱은 알림 전달을 막고, 부분 이름은 조용히 실패한다.
같은 상대를 연속으로 두 번 멘션하지 않는다. **예외는 통치자와 교육자** — 배정·되돌림 때문에 연속 호출이 정상이다.

### 2. 산출물은 어디에 두는가

**저장소는 `~/.buzz/REPOS/<프로젝트>/` 다.** 대표의 로컬 사본과는 **다른 체크아웃**이므로, 「내 쪽에 없다」는 「없다」가 아니다.

Nest 산출물은 전부 **프로젝트 폴더 아래**에 둔다 — `WORK_LOGS/<프로젝트>/` · `PLANS/<프로젝트>/` · `RESEARCH/<프로젝트>/`. Nest는 전원이 공유하고 **일반 파일에는 동시 쓰기 보호가 없다.**

- 파일명은 `<역할키>_<대상>__<ORD-ID>.md`. **덮어쓰지 않는다** — 재작업은 새 ORD이므로 새 파일이 되고, 옛 파일의 프론트매터를 `status: superseded` 로 바꾼다.
- 프론트매터의 `title` 은 **반드시 따옴표로 감싼다.** 따옴표 없는 콜론이 YAML을 깨뜨린다. `status` 는 `active`/`superseded`/`stale`/`draft` 중 하나다.
- 작업 산출물이 `PLANS/`·`RESEARCH/` 에 있더라도 **완료 보고만은 항상 `WORK_LOGS/<프로젝트>/` 에도 둔다.** 통치자가 거기를 스캔한다.
- `buzz mem` 은 이 팀에서 쓰지 않는다(통치자의 `core` 만 예외). `mem` 은 릴레이의 slug 저장소라 위 파일들과 **다른 곳**이다 — 섞으면 서로 못 찾는다.
- 역사 기록(`docs/history/`)은 **교육자만** 쓴다.

### 3. 멈추는 조건

- 다음 사람을 멘션한 뒤 **새 일을 스스로 시작하지 않는다.**
- **되돌릴 수 없는 git 조작 넷** — force push · `reset --hard` · 히스토리 재작성 · 원격 브랜치 삭제 — 이 필요해 보이면 멈추고 대표에게 보고한다.
- 내 몫의 끝은 각자 지침의 「여기서 멈춘다」에 적혀 있다.

### 4. 스킬을 부르는 법

**턴의 첫 동작으로 「내 스킬」을 호출해 본문을 로드한다.** 결과는 세 갈래다.

- **본문이 오면** 그대로 따른다.
- **「사용 가능한 스킬 목록에 없다」로 나오면** 고장도 미설치도 아니다(사람만 호출하도록 설계됐거나 목록이 예산으로 잘린 것이다). 디스크에서 본문을 찾아 읽고 절차를 그대로 따른다:
  ```
  find ~/.claude/plugins/cache -path "*/<스킬이름>/SKILL.md" | sort
  ```
  플러그인·마켓플레이스·배치 깊이를 가리지 않는 형태다. **`ls` 와 글롭은 쓰지 않는다** — 스킬 배치가 플러그인마다 달라(`skills/<이름>/` 와 `skills/<분류>/<이름>/` 가 섞여 있다) 글롭은 절반만 찾는다. 여러 판이 나오면 버전이 높은 것을 읽는다. 읽고 나면 채널에 **「스킬 호출이 아니라 본문을 읽어 따랐다」고 한 줄 밝히고** 진행한다.
- **본문 파일을 못 찾으면** 그때 이름을 대고 멈춘다.

어느 경우든 **방법은 스킬 본문이 정본**이다. 스킬 이름은 **플러그인 한정**으로 쓴다(`mattpocock-skills:code-review`) — 같은 이름의 다른 스킬이 있고 그중에는 워킹트리를 고치는 것도 있다.

### 5. 도구

**cbm (codebase-memory-mcp) — 「찾는 도구」다. 「없음을 증명하는 도구」가 아니다.**

쓰는 사람은 `IMP`·`TKT`·`DBG` 셋뿐이다.

- 찾은 것은 **반드시 그 파일을 열어 눈으로 확인한 뒤** 결과로 쓴다.
- **색인이 만들어진 커밋과 현재 `git rev-parse HEAD` 가 같은지 먼저 확인한다.** 다르면 재색인하거나 쓰지 않는다. 인용할 때 색인 시점 SHA를 함께 적는다.
- `detect_changes` 는 쓰지 않는다 — 변경 규모 신호가 뒤집힌다.
- 한국어 질의는 없는 문자열에도 전체 노드 수를 돌려준다. **0건이 나왔다는 것을 근거로 쓰지 않는다.**

**ai-memory — 조회 보조다. 게이트 근거가 아니다.**

`DBG`(이 증상을 전에 봤나) · `IMP`(이 파일을 전에 누가 어떻게 고쳤나) · `PRP`(같은 지적을 전에 했나)가 쓴다.
조회할 때 **프로젝트명을 질의에 명시**하고, 받은 것은 **원본 경로로 확인한 뒤** 쓴다. 모든 저장소의 관찰이 한 곳에 섞여 들어오기 때문이다.

### 6. 안전

- **개인정보는 합성 데이터로 쓴다.** 채널 본문은 릴레이에 평문으로 저장돼 릴레이 운영자가 읽을 수 있다.
- **비밀값은 화면에 찍지 않는다.** 확인이 필요하면 길이·앞 8자 해시·존재 여부만 낸다. 파일 전체를 보여야 하면 비밀값 줄은 **치환이 아니라 제거**한다.
- **커밋 트레일러는 둘이다.** 저장소에서 `git config user.name`/`user.email` 을 읽는다(추측·질문 금지, 비어 있으면 멈추고 묻는다).
  ```
  git commit --trailer "Co-authored-by: <이름> <메일>" --trailer "Signed-off-by: <이름> <메일>"
  ```
  `Co-authored-by` 를 먼저 둔다. 푸시 전 `git log -1` 로 확인한다.

### 7. 주인이 정해진 산출물

이슈·라벨·배정·종료·blocking 규약은 **`docs/agents/issue-tracker.md` 가 정본**이다. 여기서 다시 적지 않는다.
아래 넷만 이 파일이 정한다.

| 산출물 | 주인 | 규칙 |
|---|---|---|
| **브랜치 이름** | `IMP`·`DBG` | `feat/<티켓번호>-<짧은-슬러그>` · `fix/<증상-슬러그>`. **리뷰어·완결자의 보고 파일명은 이 브랜치 이름을 그대로 쓴다** — 같은 작업이 여러 키로 흩어지지 않게 |
| **병합 충돌 해소** | `IMP` | 완결자는 충돌을 만나면 멈추고 통치자에게 올린다. 통치자가 구현자를 다시 부른다. `mattpocock-skills:resolving-merge-conflicts` 를 쓴다 |
| **병합 후 깨진 테스트** | `IMP` | 같은 경로. 완결자는 조사해 무엇이 깨졌는지까지 적고 멈춘다 |
| **G1 이후의 ADR** | `ITG` | 구현 도중 되돌리기 어려운 결정을 만나면 구현자가 통치자에게 올리고, 통치자가 기획자를 재소환한다. 구현자가 `docs/adr/` 에 직접 쓰지 않는다 |

<!-- oss-agent-team:end -->
