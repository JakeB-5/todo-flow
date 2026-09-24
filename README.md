# TODO Flow

선택한 TODO를 작은 작업으로 나누고, 교체 가능한 에이전트가 수행하는 로컬 실행 시스템입니다. 트랙 문서와 실행 기록을 파일로 보존하므로 `rg`로 검색하고 다른 세션에서 이어갈 수 있습니다.

```text
todo 등록 → 문서 검토 → 대시보드 / track-picks 선정 → trackrun ID…
  → 필요한 조사·구현·검증·독립 리뷰 → 랜딩 → 트리아지 → 완료
```

워커가 다음에 필요한 작업을 제안합니다. 전체 트랙을 고정 S단계에 넣거나 감독 에이전트를 상주시키는 구조는 아닙니다. 호스트는 작업 소유권, 검증 근거, 외부 반영과 완료 조건을 확인합니다. 랜딩까지 허용된 프로젝트에서는 반영 뒤 트리아지와 이슈 종료가 이어집니다.

현재는 소규모 프로젝트의 전체 사이클과 복구를 검증한 개발 버전입니다. 대형 프로젝트·Forgejo·복수 저장소 운영은 추가 구현과 검증이 필요합니다. [현재 제한](#현재-제한)을 확인하세요.

에이전트에게 설치와 첫 실행을 맡길 때는 [For agents](#for-agents)를 사용하세요.

## 워크플로우 발전과 관측된 처리량

**1월부터 9월까지의 변화**를 선행 워크플로우 운영 기록에서 측정했습니다. 7월에 등록·실행 도구의 첫 기록이 확인되며, 그 직전인 **6월 대비 9월의 일평균 커밋은 13.91배, 보정 소스 변화량은 4.52배**입니다. 원장으로 비교 가능한 **7월 대비 완료 전환의 일평균은 2.30배**입니다.

![1~9월 커밋·소스 변화량·통합 머지·완료 전환의 일평균 추이](assets/metrics/workflow-growth.png)

9월은 **1~23일 실측**이며 월말 예상치를 섞지 않았습니다. 같은 길이로 환산한 일평균을 비교합니다. 소스 지표는 추가와 삭제를 합산하고 생성물·외부 소스·알려진 초기 입력 및 저장소 이동 영향을 제외했습니다.

| 일평균 비교 | 커밋 활동 | 보정 소스 변화량 | 완료 전환 |
|---|---:|---:|---:|
| 1월 → 9월 | 33.93배 | 1.09배 | 비교 불가 |
| 6월 → 9월 | 13.91배 | 4.52배 | 비교 불가 |
| 7월 → 9월 | 3.88배 | 2.10배 | 2.30배 |
| 8월 → 9월 | 1.21배 | 1.16배 | 0.86배 |

커밋 증가와 작업량 증가는 같은 숫자가 아닙니다. 1~6월의 완료 전환은 원장 부재로 측정 불가이며, 9월 완료 전환/일은 8월보다 13.7% 낮았습니다. 소스 줄 수는 투입 시간·품질을 뜻하지 않고, 완료 전환도 고유 기능 수와 구분합니다.

이 자료는 **현재 패키지의 벤치마크가 아닌 선행 운영 이력의 익명 집계**입니다. 워크플로우 변화와 함께 관측된 증가를 보여주며, 다른 조건을 통제하지 않았으므로 개발 효과만의 인과적 배수라고 주장하지 않습니다.

<details>
<summary>소스 추가·삭제와 제외량 보기</summary>

![월별 소스 추가·삭제와 생성물·이동 등의 제외량](assets/metrics/source-changes.png)

회색은 보정 소스 지표에서 제외한 변경입니다. 막대 위 숫자는 포함된 소스 추가+삭제 합계입니다. 1월·7월의 소스 일평균에서는 알려진 초기 입력·이동일을 분모에서도 제외했습니다.

</details>

[측정 방법·월별 실측표](assets/metrics/README.md) · [집계 JSON](assets/metrics/measurements.json) · [CSV](assets/metrics/monthly.csv)

## 구성

공용 엔진 프로그램을 설치하고 프로젝트마다 설정·데이터·스킬을 준비합니다. 실행 프로세스와 대시보드 서버는 프로젝트별로 뜹니다.

| 구성 | 역할 |
|---|---|
| `todo-flow` | 프로젝트 초기화, 문서 등록, 실행·제어·복구, 대시보드 |
| `trackrun` | 사용자가 선정한 트랙의 실행 요청과 워커 기동 |
| 프로젝트의 `todo/` | 트랙·문서 revision·작업·결과·외부 반영 기록 |
| `.agents/skills/` 또는 `.claude/skills/` | 프로젝트 세션에서 사용하는 등록·선정·실행 스킬 |

## 설치

필요한 도구는 Python 3.11 이상, `uv`, Git, 로그인된 Claude CLI 또는 Codex CLI입니다. GitHub 이슈·PR을 사용한다면 GitHub CLI(`gh`)와 해당 저장소에 대한 인증도 필요합니다.

이 저장소 루트에서 실행합니다.

```sh
uv sync --frozen
uv run todo-flow --help
uv tool install .
```

`uv tool install .`은 다른 프로젝트의 터미널에서도 `todo-flow`와 `trackrun`을 사용할 수 있게 합니다. 설치하지 않고 개발할 때는 이 저장소에서 `uv run todo-flow` 또는 `uv run trackrun`을 사용할 수 있습니다.

## For agents

이 README를 전달받은 에이전트는 사용자가 지정한 프로젝트에서 설치·설정·첫 트랙 안내를 직접 수행하세요. 이미 설치된 부분은 재사용하고, 아래에서 필요한 작업만 진행합니다. 이 안내를 읽는 것만으로 설치나 실제 실행이 승인되는 것은 아니며 사용자의 요청 범위와 기존 권한을 따릅니다.

사용자는 다음처럼 요청할 수 있습니다.

```text
이 README의 For agents에 따라 /absolute/my-project에 TODO Flow를 설치해줘.
첫 작업은 [원하는 변경과 기대 결과]야.
HTML TODO를 등록하고 문서 링크를 보여줘. 이 요청의 범위를 충족하는 트랙을
선정해서 첫 런까지 진행하고, 실제 결과와 남은 질문을 알려줘.
```

**설치·설정·검증을 진행할 수 있으면 먼저 수행하고, 사용자가 직접 해야 하는 로그인이나 결정할 수 없는 작업 범위만 짧게 물으세요.** 이미 주어진 실행·랜딩 권한을 다시 묻지 않습니다. 설치만 요청받았다면 준비된 대시보드와 첫 TODO 요청 방법까지 제공하고, 첫 요구가 없으면 실제로 처리할 요구를 요청합니다. 임의의 샘플 트랙을 운영 프로젝트에 등록하지 않습니다.

### 1. 대상 프로젝트와 기존 설정 확인

TODO Flow 소스 위치와 적용할 프로젝트 위치를 구분합니다. 이 README가 있는 저장소를 자동으로 실행 대상에 삼지 않습니다. 아래 경로는 실제 경로로 바꾸고 같은 값을 이후 단계에서도 사용하세요.

```sh
FLOW_SOURCE='/absolute/todo-flow'
FLOW_PROJECT='/absolute/my-project'
FLOW_STATE="$FLOW_PROJECT/todo"
FLOW_SKILLS="$FLOW_PROJECT/.agents/skills"

git -C "$FLOW_PROJECT" rev-parse --show-toplevel
git -C "$FLOW_PROJECT" status --short
git -C "$FLOW_PROJECT" rev-parse --verify HEAD
```

새 터미널이나 개별 셸 호출에서 변수가 유지된다고 가정하지 마세요. 필요한 변수를 다시 설정하거나 실제 절대 경로를 명령 인자로 전달합니다.

대상 프로젝트의 `AGENTS.md`와 개발 안내, 패키지·검증 명령, 원격과 기준 브랜치, 기존 스킬, `FLOW_STATE/config/1.json` 유무를 읽습니다. 기존 설정이 있으면 연결된 저장소·워커·종료점을 확인해 재사용하고 `init`을 반복하지 않습니다. 같은 `todo/`가 다른 도구의 원장이면 덮어쓰거나 자동 변환하지 않고 별도 상태 경로를 정합니다.

GitHub 저장소 식별자와 base는 실제 원격에서 확인합니다. 현재 지원하지 않는 Forgejo·복수 저장소 작업을 지원한다고 가정하지 않습니다. 초기 커밋이나 원격이 없다면 현재 요청에 맞는 준비가 필요한지 판단하고, 기존 사용자 변경을 일괄 커밋하거나 Git 이력을 초기화하지 않습니다.

### 2. 실행 프로그램과 인증 준비

Python 3.11+, uv, Git과 사용할 에이전트 CLI를 확인하고, GitHub 연동에는 gh도 확인합니다. 필요한 도구가 없으면 해당 환경의 설치 방식으로 준비합니다. 기존에 인증된 워커와 사용자가 지정한 워커를 우선하며 모델을 임의로 바꾸지 않습니다. 로그인은 해당 CLI의 인증 흐름으로 진행하고 자격 증명을 문서·설정·로그에 복사하지 않습니다.

새 설치는 TODO Flow 체크아웃에서 수행합니다.

```sh
cd "$FLOW_SOURCE"
uv sync --frozen
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --help
trackrun --help
```

이미 호환되는 설치가 있으면 재사용합니다. 명령이 보이지 않으면 설치된 실행 파일 경로와 현재 세션의 PATH부터 확인합니다. 소스가 달라 재설치가 필요한 경우에는 다른 프로젝트에서 실행 중인 엔진과의 영향을 확인합니다. 첫 설치를 위해 기존 공용 엔진이나 같은 이름의 스킬을 무조건 강제 교체하지 않습니다.

### 3. 프로젝트 실행 계약과 스킬 설치

[프로젝트 시작](#프로젝트-시작)의 `init` 예시를 대상 프로젝트에 맞게 구성합니다. 새 설정에 필요한 값은 다음과 같습니다.

| 값 | 에이전트가 확인할 근거 |
|---|---|
| `--repo`, `--state` | 실제 대상 Git 루트와 해당 프로젝트의 파일 정본 경로 |
| `--github`, `--base` | 실제 GitHub owner/repository와 기준 브랜치. 다른 서비스 이름을 GitHub 인자로 넣지 않음 |
| `--worker` | 사용자가 선택했거나 사용 가능한 인증된 Claude/Codex CLI |
| `--verify` | 프로젝트에 존재하는 검증 명령의 JSON argv. 별도 인터프리터가 필요하면 절대 경로 사용 |
| `--context`, `--write` | 첫 작업에 필요한 소스·테스트·설정 범위. 저장소 전체나 비밀 파일을 무조건 포함하지 않음 |
| 종료점 | 신규 프로젝트는 기본 `review`. 랜딩까지 허용된 요청에는 `--endpoint land --allow-land` 적용 |

설정 전에 검증 명령이 대상 프로젝트에서 실제로 실행되는지 확인합니다. 기존 실패는 근거와 함께 구분하고 명령을 성공으로 꾸미지 않습니다. 설정이 이미 있는 프로젝트는 현재 종료점·검증·범위를 존중하며, 실행 중 설정을 직접 바꾸지 않습니다.

프로젝트 안에 상태를 둘 경우 기존 보관 정책을 확인하고 런타임 파일이 실수로 커밋되지 않도록 로컬 Git 제외 규칙 등을 설정합니다. 이미 버전 관리하는 트랙 원장을 임의로 추적 해제하지 않습니다.

```sh
# Claude 세션이면 FLOW_SKILLS를 프로젝트의 .claude/skills로 지정
todo-flow install-skills --target "$FLOW_SKILLS"
```

설치 대상의 같은 이름 스킬을 먼저 확인합니다. 이미 같은 내용이면 다시 설치할 필요가 없습니다. 충돌하면 전체 폴더 삭제 대신 차이를 확인하고 현재 설치 요청의 범위에서 보존·이전합니다. 스킬 자동 탐지가 아직 안 되는 세션에서는 설치된 `SKILL.md`를 직접 읽어 같은 절차를 수행할 수 있습니다. 다음 세션에서도 자동 탐지되는지와 실제 설치 경로를 사용자에게 알려줍니다.

### 4. 대시보드 기동과 첫 HTML TODO 등록

별도 터미널 또는 세션 종료와 독립적으로 유지되는 실행 방식으로 서버를 띄우고, 실제 응답과 연결된 프로젝트를 확인합니다. 아래 포트가 다른 프로젝트에서 사용 중이면 그 프로세스를 종료하지 말고 빈 포트를 사용하세요.

```sh
todo-flow --state "$FLOW_STATE" serve --port 8765
```

사용자에게 실제 대시보드 URL을 제공합니다. 설치된 `todo/SKILL.md`를 읽고 사용자의 첫 요구를 조사해 기존 트랙·Watch와 중복을 확인합니다. 설치된 `todo/assets/track.html` 또는 소스의 HTML 템플릿으로 정본 밖에 초안을 작성하고 CLI로 등록합니다. 자산이 없으면 `--assets`는 생략합니다.

```sh
todo-flow --state "$FLOW_STATE" register /absolute/scratch/first-track.html
```

등록 응답의 **실제 ID와 revision**으로 문서 URL을 만듭니다: `http://127.0.0.1:PORT/documents/ID/REVISION/index.html`. 브라우저에서 본문·그림·필요한 조작을 확인하고 사용자가 읽을 링크를 제공합니다. 확인할 수 없었다면 시각 검증을 완료했다고 보고하지 않습니다.

### 5. 선정과 첫 런

설치된 `track-picks/SKILL.md`로 현재 상태·의존·가능 범위를 읽어 후보 ID를 제시합니다. 사용자가 이미 첫 요구의 선정·실행까지 요청했다면 그 범위에서 진행합니다. 등록이나 추천만 요청받았다면 문서를 제시하고 사용자의 선정·실행 요청을 기다립니다. 필요한 권한이 새로 생길 때만 무엇이 부족한지 구체적으로 확인합니다.

실행할 때는 `trackrun/SKILL.md`를 읽고 **실제 등록된 ID**를 사용합니다. 아래 ID는 자리표시자이며 그대로 실행하지 않습니다.

```sh
trackrun --state "$FLOW_STATE" FIRST_TRACK_ID
```

기본 동시성으로 시작할 수 있으므로 `--jobs`는 필수가 아닙니다. 접수만 하는 `--request-only`를 사용했다면 별도 실행기가 실제로 동작하는지도 확인해야 합니다. 첫 실행을 요청 저장만으로 성공 처리하지 않습니다.

### 6. 결과 확인과 사용자 인계

트랙의 `state.json`, `tasks/`, `attempt-records/`, `results/`, `decisions/`, `effects/`를 읽고 설정한 종료점에 도달했는지 확인합니다. GitHub 연동이면 실제 이슈·PR 상태도 대조합니다. `review` 종료점은 리뷰된 후보 준비까지이며 랜딩·트랙 완료와 구분합니다. `land` 종료점은 반영 SHA와 최신 cleared 트리아지, 완료 상태 및 해당 이슈 종료까지 확인합니다.

실행이 멈췄다면 설정을 다시 초기화하지 말고 원인을 확인합니다. 재시작·작업 배정 상한으로 남은 일이 있으면 같은 상태의 `run`으로 이어가고, 결정이 필요하면 `answer`로 실제 답변을 기록한 뒤 재개합니다. 기술 오류를 임의의 사용자 답변으로 덮지 않습니다. 실패·복구 이력과 파생 TODO의 선정 대기를 유지합니다.

사용자에게는 **설치 위치·상태 경로, 대시보드·트랙 문서 링크, 실제 실행 결과·이슈/PR, 남은 질문과 다음 명령**을 간단히 전달합니다. 실행 중인 터미널이나 프로세스를 남겼다면 그 위치도 포함합니다. 설치만 완료했을 때는 다음 요청 예시를 알려줍니다: `todo [첫 요구]` → `trackpicks` → `trackrun [선정한 ID]`.

## 프로젝트 시작

대상 Git 저장소에는 초기 커밋, `origin`, Git 작성자 설정이 있어야 합니다. 아래는 Python 프로젝트 예시입니다. 경로·검증 명령·읽기 및 쓰기 범위를 대상 프로젝트에 맞게 지정하세요.

```sh
todo-flow --state /absolute/project/todo init \
  --repo /absolute/project \
  --github owner/repository \
  --base main \
  --worker codex \
  --verify '["python3","-m","unittest","discover","-v"]' \
  --write 'src/*.py' --write 'tests/*.py' \
  --context 'src/*.py' --context 'tests/*.py' --context README.md

todo-flow install-skills --target /absolute/project/.agents/skills
```

Claude 세션용 설치 위치는 `/absolute/project/.claude/skills`입니다. 같은 이름의 기존 스킬이 있으면 설치기는 덮어쓰지 않습니다. 현재 기본 워커는 Claude이며 `--worker codex`로 바꿀 수 있습니다. 모델을 지정하지 않으면 CLI의 기본 모델을 사용합니다.

기본 종료점은 `review`입니다. 승인된 자동 랜딩을 사용하려면 초기화에 `--endpoint land --allow-land`를 추가합니다. `--github`를 생략하면 GitHub 이슈·PR 없이 Git 원격을 대상으로 실행할 수 있습니다. 현재 외부 서비스 어댑터는 GitHub입니다.

`--verify`는 셸 문자열이 아닌 JSON argv입니다. 검증 명령은 호스트에서 실행됩니다. `--context`는 워커에게 전달할 텍스트 파일, `--write`는 허용할 변경 경로를 정합니다. 자격 증명을 이 범위나 트랙 문서에 넣지 마세요. 현재 실행 중 설정 변경은 지원하지 않습니다.

## 사람 관점의 사용 흐름

1. 프로젝트의 에이전트 세션에서 `todo` 스킬로 요구를 전달합니다. 에이전트가 기존 트랙·Watch·소스를 검색하고 검토할 HTML 문서를 등록합니다. 등록만으로 실행이나 외부 이슈 생성은 일어나지 않습니다.
2. 문서를 열어 목표·범위·완료 조건·도해를 검토합니다. 대시보드에서 고르거나 `trackpicks`로 실행 후보를 추천받습니다. 선정 자체는 읽기 전용입니다.
3. 같은 프로젝트의 세션에서 `trackrun track-a track-b`로 명시적으로 실행합니다. 워커가 작업·검증·리뷰를 수행하고, 허용된 종료점까지 진행합니다.
4. 대시보드에서 진행 상황과 질문을 확인합니다. 랜딩 후 트리아지는 원래 범위의 결함을 재작업으로, 별도 요구를 기존/신규 TODO로, 조건부 관찰을 Watch로 연결합니다. 새로 등록된 후속 TODO는 사용자 선정 전 실행하지 않습니다.

```sh
# 프로젝트 디렉터리에서 실행: 기본 상태 경로는 ./todo
trackrun track-a track-b

# 별도 터미널에서 대시보드 실행
todo-flow serve --port 8765
```

대시보드는 `http://127.0.0.1:8765`에서 열립니다. 여러 프로젝트를 동시에 열 때는 서로 다른 포트를 지정합니다. 다른 디렉터리에서는 `trackrun --state /absolute/project/todo track-a`처럼 정본 위치를 지정합니다.

`--jobs N`은 해당 드라이버의 동시 작업 상한이며 기본값은 2입니다. 트랙 수나 고정 워커 수를 뜻하지 않습니다. `--max-tasks` 기본값 100은 한 실행기의 작업 배정 상한입니다. 상한에 도달해도 남은 요청은 보존되며 다음 실행에서 이어갑니다.

이미 별도 실행기를 운영한다면 `trackrun ID --request-only`로 접수만 할 수 있습니다. `todo-flow run --daemon`은 새 작업을 기다리는 실행 모드이며 작업 배정 상한은 여전히 적용됩니다.

## 문서와 파일 정본

[HTML 템플릿](templates/track.html), [Markdown 템플릿](templates/track.md), [JSON 예시](templates/track.json)를 제공합니다. 사람이 보는 분석·계획 본문은 자유롭게 작성하고, 실행용 목표·범위·근거·조건 ID를 함께 기록합니다.

```sh
todo-flow --state /absolute/project/todo register /tmp/track.html \
  --assets /tmp/track-assets
```

HTML 원문과 SVG·이미지·CSS·JavaScript·Three.js 자산을 보존합니다. Markdown도 원문을 보존하고 HTML로 렌더합니다. 자산은 문서에서 `assets/...`로 참조하고 revision별로 묶습니다. HTML의 실행 계약은 `todo-flow-track` ID를 가진 `application/json` 블록입니다.

```text
todo/
  config/1.json                    프로젝트 실행 설정
  tracks/<id>/track.html            검토할 HTML 원문 또는 렌더
  tracks/<id>/source.md             Markdown 입력의 원문
  tracks/<id>/assets/               문서 자산
  tracks/<id>/state.json            현재 상태·요청·실행 참조
  tracks/<id>/revisions/            과거 문서와 자산
  tasks/ · attempt-records/         작업과 워커 실행 시도
  results/ · decisions/ · events/   결과·질문·사건 기록
  effects/                         외부 반영 의도와 영수증
  findings/ · triages/ · watches/   발견·트리아지·조건부 관찰
  attempts/                        워커 원문 입력·출력·진단
  .cache/query.sqlite              삭제 후 재생성 가능한 조회 캐시
```

정본은 HTML·Markdown·JSON 파일입니다. SQLite는 내부 조회 캐시이며 에이전트에게 SQL 사용을 요구하지 않습니다. 문서 수정은 현재 revision을 읽고 `register FILE --expected-revision N`으로 등록합니다. 정본을 직접 덮어써 이전 검증을 재사용하지 않습니다.

대시보드의 문서 크게 열기 링크는 `/documents/ID/REVISION/index.html`로 연결됩니다. 로컬 ES module과 Three.js는 `file://` 대신 이 HTTP 경로로 확인합니다. 등록 후 실제 브라우저 렌더와 필요한 상호작용을 검토합니다.

## 현황과 제어

- 활성 TODO는 페이지 버튼 없이 연속 목록으로 표시합니다. 완료 트랙은 별도 검색 화면에서 조회합니다.
- 등록·수정은 에이전트 스킬에서 수행합니다. 대시보드는 선정한 `trackrun` 명령을 복사하며 직접 실행 요청을 보내지 않습니다.
- 실행 현황에는 현재 작업·역할·최근 관측·대기와 질문을 표시합니다. 연결 끊김이나 작업권 만료를 완료로 표시하지 않습니다.
- 문서·검증·리뷰·랜딩·트리아지 근거는 트랙 상세에서 확인합니다.

```sh
todo-flow --state STATE status
todo-flow --state STATE pause track-a
todo-flow --state STATE resume track-a
todo-flow --state STATE cancel track-a
todo-flow --state STATE answer DECISION_ID --text '판단 또는 확인된 복구 근거'
todo-flow --state STATE run
```

드라이버가 종료돼도 같은 `STATE`로 다시 실행하면 파일에 남은 작업을 이어갑니다. 이전 작업권의 늦은 응답은 반영하지 않으며, 외부 효과는 원격과 영수증을 대조해 중복을 방지합니다. `pause`, `resume`, `answer` 뒤 실행기가 없다면 `run`으로 필요한 처리를 재개합니다.

Watch는 `watches`로 조회하고 `signal TRIGGER --version VERSION`으로 재확인할 입력을 알립니다. `watch-dispose WATCH_ID --status resolved|dismissed|promoted --evidence TEXT`로 처분하며 승격에는 `--target TRACK_ID`도 필요합니다. 비활성 트랙의 신호만으로 새로운 코드 실행을 시작하지 않습니다.

완료한 깨끗한 작업 공간은 `todo-flow --state STATE cleanup TRACK_ID`로 명시적으로 회수할 수 있습니다. 기존 SQL 상태를 새 파일 정본으로 복사할 때는 `migrate-files --source OLD --target NEW`를 사용합니다. 이는 다른 도구의 JS 원장을 가져오는 도구는 아닙니다.

## 반영과 완료

리뷰는 구현 대화를 공유하지 않는 새 에이전트가 정확한 후보와 검증을 읽고 수행합니다. PR 작성자와 같은 GitHub 계정이면 판정을 COMMENT 리뷰로 게시하며 다른 사람의 APPROVE라고 표현하지 않습니다.

랜딩은 최신 base와 후보를 독립 체크아웃에서 합쳐 검증한 커밋을 반영합니다. 기준 브랜치가 그사이 전진하면 재대조하며 보호 브랜치 정책을 우회하지 않습니다. 확인된 랜딩 뒤 최신 트리아지 영수증이 있어야 이슈 종료와 완료를 채택합니다. 원래 완료 조건을 후속 TODO나 Watch로 옮겨 완료 처리할 수 없습니다.

## 현재 제한

- 프로젝트 상태 하나는 Git 저장소 하나를 관리합니다. Forgejo 및 서브모듈·복수 저장소 결합 반영은 아직 지원하지 않습니다.
- 기본 실행 워커는 도구 없는 파일 스냅샷을 받아 JSON 변경안을 반환합니다. 호스트가 파일 변경·검증·GitHub 반영을 수행합니다. 워커가 직접 저장소 검색·셸·브라우저 도구를 사용하는 개발 모드는 아직 없습니다.
- 파일 입력 총량은 150,000바이트로 제한됩니다. 파일 삭제·바이너리 수정은 현재 지원하지 않습니다.
- 기본 워커 제한 시간은 600초입니다. 실행 슬롯과 무거운 검증 자원을 분리하는 큐, 여러 드라이버의 합산 슬롯 제어는 아직 없습니다.
- 작은 독립 트랙 2~3개 실제 동시 실행과 합성 대량 목록을 확인했습니다. 대형 코드베이스·대량 동시 갱신·분산 파일시스템 운영까지 검증한 것은 아닙니다.

## 개발과 기여

설치·테스트·외부 실험·PR 작성 방법은 [CONTRIBUTING.md](CONTRIBUTING.md), 변경 사항은 [CHANGELOG.md](CHANGELOG.md), 에이전트 작업 규칙은 [AGENTS.md](AGENTS.md)를 참고하세요.

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
node --check src/todo_flow/web/app.js
uv build
```

`docs/`는 로컬 설계·실험 기록용입니다. Git과 배포 패키지에서 제외하며, 새 체크아웃의 사용·개발에 필요한 정보는 위 루트 문서에 유지합니다. 트랙 템플릿·스킬·대시보드 소스는 계속 버전 관리합니다.
