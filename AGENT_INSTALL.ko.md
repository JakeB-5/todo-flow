# TODO Flow — 에이전트 설치와 첫 실행

[English](AGENT_INSTALL.md) · [한국어](AGENT_INSTALL.ko.md) · [日本語](AGENT_INSTALL.ja.md) · [简体中文](AGENT_INSTALL.zh-CN.md)

[README](README.ko.md) · [운영](OPERATIONS.ko.md) · [업데이트](UPDATES.ko.md) · [데모](DEMO.ko.md)

<!-- translation-source: AGENT_INSTALL.md; source-sha256: 91a1c6b7bd92fffce08b72274b66cfd8aa79147fa24d24cf049ce5c283855ed8; status: translated -->

사용자가 요청한 범위를 따르세요. 기존 설치, 설정과 권한을 재사용하세요. 이 문서 자체는 작업 실행이나 변경 사항의 랜딩을 허가하지 않습니다. 승인된 되돌릴 수 있는 준비를 완료하고, 빠진 결정이나 사용자가 직접 해야 하는 인증만 요청하세요.

## 1. 대상과 언어 확인

TODO Flow 체크아웃과 사용자의 대상 프로젝트를 구분하세요. 대상의 `AGENTS.md`, 개발 지침, 검증 명령, Git 원격, 기준 브랜치, 기존 스킬과 상태 설정을 읽으세요.

**주 언어는 사용자가 선택합니다. 영어(`en`), 한국어(`ko`), 일본어(`ja`), 중국어 간체(`zh-CN`)를 지원합니다.** 기존 프로젝트 언어나 이번 요청의 명시적인 선호를 재사용하세요. 없다면 사용할 언어를 사용자에게 물으세요. 답을 기다리는 동안 독립적인 환경 조사는 계속하되, 이 README의 언어만 보고 조용히 결정하지 마세요. 스킬 지침은 영어로 유지됩니다. 보고, 질문과 새 문서는 명시적으로 달리 요청하지 않는 한 선택한 언어를 사용합니다.

```sh
FLOW_SOURCE='/absolute/todo-flow'
FLOW_PROJECT='/absolute/my-project'
FLOW_STATE="$FLOW_PROJECT/todo"
FLOW_SKILLS="$FLOW_PROJECT/.agents/skills"

git -C "$FLOW_PROJECT" rev-parse --show-toplevel
git -C "$FLOW_PROJECT" status --short
git -C "$FLOW_PROJECT" rev-parse --verify HEAD
```

Claude 세션은 `.claude/skills`를 사용하세요. 별도 셸 호출 사이에는 변수가 유지되지 않을 수 있으므로 다시 선언하거나 절대 경로를 사용하세요. `todo/`를 다른 도구가 사용한다면 덮어쓰지 말고 별도 상태 디렉터리를 선택하세요. 기존 변경 사항, Git 이력과 스킬 설치를 보존하세요.

## 2. 도구와 인증 준비

Python 3.11 이상, uv, Git, 선택한 인증된 Claude/Codex CLI를 확인하세요. GitHub 연동에는 인증된 `gh`와 실제 대상 원격에 대한 접근 권한도 필요합니다. 사용자 요청에 근거가 없다면 모델을 바꾸지 마세요. 자격 증명을 문서, 설정과 출력에 넣지 마세요.

릴리스를 새로 설치할 때는 체크아웃이 필요하지 않습니다.

```sh
uv tool install https://github.com/JakeB-5/todo-flow/releases/download/v0.0.9/todo_flow-0.0.9-py3-none-any.whl
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --version
trackrun --version
```

공식 저장소는 [https://github.com/JakeB-5/todo-flow](https://github.com/JakeB-5/todo-flow)이며, 릴리스 파일과 체크섬은 [GitHub Releases](https://github.com/JakeB-5/todo-flow/releases)에 있습니다. 소스 개발을 요청받았다면 TODO Flow 체크아웃에서 설치하세요.

```sh
cd "$FLOW_SOURCE"
uv sync --frozen
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --help
trackrun --help
```

호환되는 기존 설치를 재사용하세요. 재설치 전에 PATH를 진단하세요. 공유 엔진을 교체하기 전에 실행 중인 다른 프로젝트를 고려하세요. 문서에 명시된 GitHub 릴리스 wheel을 사용하고, 다른 인덱스의 동명 패키지가 이 프로젝트라고 가정하지 마세요.

`0.0.2`는 필요할 때 파일을 읽는 기능과 터미널을 우선하는 워커를 추가합니다. 기존 `0.0.1` 설치에서 이 기능을 쓰려면 엔진과 프로젝트 스킬을 업데이트해야 합니다. [업데이트 안내](UPDATES.ko.md)를 따르세요. 새 런처의 기본값은 `auto`입니다. Orca, 설정된 터미널 또는 기존 tmux를 사용하고, 그다음 headless로 실행합니다. 워커가 자동으로 동작한다는 이유만으로 headless를 강제하지 마세요. [워커 실행](OPERATIONS.ko.md#worker-context-and-terminal-launchers)을 참고하세요.

`0.0.3`은 통합 수정을 현재 기준 브랜치에 연결하고 랜딩 전에 새로운 검증과 독립 리뷰를 요구합니다. 이 수정을 적용하려면 공유 엔진을 업그레이드하세요. [수정 동작](OPERATIONS.ko.md#review-landing-and-completion)을 참고하세요.

`0.0.9`에는 제안 커밋 격리, 정확한 후보 체크아웃 확인, 선언된 검증 입력의 동일성 확인, 영속적인 프로세스 정리, 개수 제한 없는 실행별 터미널 정리와 네이티브 Orca/Codex 세션 지원이 포함됩니다. 워커에는 기본 시간 제한이 없습니다. 네이티브 사이드바 상태, 진행 중 실행 취소와 소유권이 검증된 워크트리 정리는 소유권 경계를 유지합니다. 체크아웃의 기존 수동 변경 사항은 보존되며 복구 결정이 필요할 수 있습니다. 자동으로 초기화하거나 스테이징하지 마세요.

## 3. 프로젝트 설정

이미 초기화되어 있다면 기존 상태 설정을 읽고 워커, 언어, 검증, 범위와 엔드포인트를 재사용하세요. 실행 중에 `init`을 다시 실행하거나 실행 설정을 수정하지 마세요.

새 프로젝트에서는 실제 프로젝트에서 값을 확인하세요.

| 설정 | 출처 |
|---|---|
| `--repo`, `--state` | 대상 Git 루트와 별도의 프로젝트 정본 상태 |
| `--language en`, `--language ko`, `--language ja`, `--language zh-CN` | 사용자가 선택한 주 언어 |
| `--base`, 선택 사항인 `--github` | 실제 원격/기준 브랜치와 GitHub 소유자/저장소 |
| `--worker` | 사용자가 선택했거나 사용할 수 있는 인증된 Claude/Codex CLI |
| `--verify` | 실제로 동작하는 기존 검증 명령을 JSON argv로 표현한 값 |
| `--context`, `--write` | `0.0.2`의 탐색 힌트(`0.0.1`에서는 스냅샷 선택)와 승인된 쓰기 패턴 |
| 엔드포인트 | 기본값은 `review`. 랜딩이 이미 승인됐다면 `--endpoint land --allow-land` 사용 |

검증 명령을 설정하기 전에 실행하세요. 기존 실패를 숨기지 말고 보고하세요. 비밀 파일은 제외하세요. 실행에는 초기 커밋, Git 작성자 정보와 `origin`이 필요합니다. 이를 포함하는 요청 없이 누락된 원격을 새로 만들거나, 무관한 작업을 커밋하거나, 이력을 초기화하지 마세요.

Python 프로젝트 예시입니다. 값과 언어를 바꾸세요.

```sh
todo-flow --state "$FLOW_STATE" init \
  --repo "$FLOW_PROJECT" --base main --worker codex --language en \
  --verify '["python3","-m","unittest","discover","-v"]' \
  --write 'src/*.py' --write 'tests/*.py' \
  --context 'src/*.py' --context 'tests/*.py' --context README.md
```

지원되는 GitHub 어댑터를 사용할 때만 `--github OWNER/REPOSITORY`를 추가하세요. Forgejo와 여러 저장소를 묶은 전달은 구현되어 있지 않습니다. 프로젝트의 기존 정책이나 로컬 Git 제외 설정으로 런타임 기록이 실수로 커밋되지 않게 보호하세요. 이미 버전 관리 중인 트랙 원장을 추적 해제하지 마세요.

## 4. 스킬 설치와 대시보드 열기

```sh
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS"
todo-flow --state "$FLOW_STATE" serve --port 8765
```

설치기는 기존 디렉터리를 보존하고 각 설치된 스킬의 `project.json`에 언어와 상태를 기록합니다. 초기화된 프로젝트의 언어를 상속합니다. 독립 설치는 `--language en|ko|ja|zh-CN`을 지원하며, 초기화된 프로젝트와 충돌하는 명시적 값은 거부합니다. 충돌 하나를 해결하려고 스킬 디렉터리 전체를 교체하지 마세요. 기존 설치에서는 `update-skills --target PATH --dry-run`으로 충돌을 검토한 뒤 요청된 업데이트 범위 안에서만 적용하세요. 매니페스트 인수와 롤백은 [UPDATES.ko.md](UPDATES.ko.md)를 참고하세요.

<a id="coexist-with-occupied-skill-names"></a>

### 이미 사용 중인 스킬 이름과 공존

충돌이 없다면 기존 아홉 이름을 모두 유지하세요. `todo`, `track-picks`, `trackrun`, `track-run`, `watchlist`, `track-work`, `track-review`, `track-land`, `track-triage`입니다. 별칭은 이미 사용 중이면서 이 TODO Flow 설치가 소유하지 않는 정식 이름에만 사용할 수 있습니다. `todo-flow install-skills --help`에 `--alias`가 나오는지 확인하세요. 오래된 릴리스라면 승인된 설치/업데이트 절차를 통해 이 기능을 포함한 소스 빌드가 필요할 수 있습니다.

먼저 대상을 조사하세요. 다음 예시는 다른 워크플로가 정확히 `todo`, `track-picks`, `track-run`, `watchlist`를 사용하고, 제안한 네 별칭은 모두 사용되지 않는다고 가정합니다. 실제 충돌에 대한 별칭만 포함하세요. Codex에는 `.agents/skills`, Claude에는 `.claude/skills`를 사용하세요.

```sh
FLOW_SKILLS="$FLOW_PROJECT/.claude/skills"
# Choose this separate STATE before initialization if another tool owns todo/.
FLOW_STATE="$FLOW_PROJECT/todo-flow-state"
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS" \
  --alias todo=flow-todo \
  --alias track-picks=flow-track-picks \
  --alias track-run=flow-track-run \
  --alias watchlist=flow-watchlist
```

초기화된 TODO Flow 프로젝트라면 기존 STATE를 유지하세요. 새 프로젝트라면 설치 전에 선택한 별도 STATE로 3단계를 실행하세요. 다른 도구의 상태 위에 초기화하지 마세요. 설치기는 그 도구의 `project.json`을 TODO Flow 컨텍스트로 가져오지 않습니다.

별칭이 이미 사용 중이면 스킬 파일을 변경하기 전에 설치 전체가 실패합니다. 오류에는 역할과 진입점이 표시됩니다. 사용되지 않는 별칭이나 에이전트가 지원하는 별도 대상을 선택하세요. 기존 파일과 심볼릭 링크를 보존하세요. 다른 워크플로를 가져오기 위해 `--adopt`를 사용하지 마세요. 인수는 일치하는 구형 TODO Flow 번들에만 적용됩니다. 충돌이 없는 역할의 이름을 바꾸지 마세요.

반환된 `entrypoints` 매핑(정식 역할 → 설치된 이름), `state`, `language`를 확인한 뒤 각 설치된 이름의 `SKILL.md`와 옆의 `project.json`을 읽으세요. 예시에서는 에이전트에 `flow-todo`, `flow-track-picks` 또는 `flow-watchlist`를 사용하도록 요청하세요. 원래 이름은 여전히 다른 워크플로의 것입니다. `flow-track-run`은 변경되지 않은 `trackrun` 스킬로 연결됩니다. 설치된 역할 표는 아홉 TODO Flow 역할을 모두 실제 진입점에 연결합니다. Claude에서는 대상 프로젝트에서 연 세션으로 이름을 확인하세요. 검색 결과가 갱신되지 않았다면 새 세션을 열거나 `.claude/skills/flow-todo/SKILL.md`를 명시적으로 읽으세요.

별칭은 셸 명령을 바꾸지 않습니다. 계속 `todo-flow --state "$FLOW_STATE" ...`와 `trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID`를 사용하세요. 설치 매니페스트를 보존하세요. 업데이트는 `--alias`를 반복하지 않아도 저장된 매핑을 재사용합니다.

```sh
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --dry-run
# After reviewing the plan and resolving any conflicts:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS"
# Only when an interrupted update is reported:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --recover
# To undo a completed update, use its actual returned backup ID:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --rollback BACKUP_ID
```

위 명령은 먼저 계획과 충돌을 검토한 뒤 적용합니다. 중단된 업데이트가 보고된 경우에만 `--recover`를 사용하세요. 완료된 업데이트를 되돌리려면 실제로 반환된 백업 ID를 사용하세요.

번들 파일이 바뀌지 않았다면 로컬 수정은 보존됩니다. 로컬과 번들이 동시에 바뀌면 조정을 위해 업데이트 전체를 중단합니다. 롤백은 이후의 수정을 버리는 대신 거부합니다. 복구와 롤백은 백업에서 소유한 파일, 매핑과 컨텍스트를 복원하며 다른 워크플로를 관리하지 않습니다. 알 수 없는 매니페스트 형식은 거부됩니다. 이 검사를 우회하려고 매니페스트를 삭제하지 마세요.

`tests/test_skill_coexistence_bundle.py`의 일곱 회귀 테스트는 `.agents/skills`와 `.claude/skills` 양쪽에서 실제 아홉 스킬 번들의 일회용 복사본을 사용합니다. 기존 기본 이름 유지, 동시 충돌 네 건, 이미 사용 중인 별칭 거부, 기존 파일과 심볼릭 링크, 설치된 frontmatter, 역할 링크, 템플릿 바이트, STATE와 CLI 예시 보존을 확인합니다. 별칭 업데이트 테스트는 로컬 수정, 기준선과 매핑 유지, 전체 업데이트 충돌 거부, 중단 복구, 롤백과 알 수 없는 매니페스트 형식을 확인합니다. `uv run python -m unittest discover -s tests -p 'test_skill_coexistence_bundle.py' -v`로 실행하고 평가 중인 후보의 실제 결과를 보고하세요. 통과는 파일시스템 배치와 메타데이터의 증거입니다. 이 테스트는 Claude를 시작하거나 세션의 스킬 검색을 조사하거나 모델을 호출하지 않습니다. 실제 Claude 검색과 모델 호출은 별도로 보고해야 하며, 이 fixture가 이를 수행하거나 암시하지 않습니다.

대시보드는 지속적으로 유지되는 터미널/프로세스에서 실행하세요. 포트가 사용 중이라면 무관한 서버를 종료하지 말고 빈 포트를 사용하세요. 실제 URL, 프로젝트와 기본 언어를 확인하세요. 표시 언어 전환은 브라우저 로컬이며 프로젝트별로 적용됩니다. 워커 언어를 바꾸거나 과거 문서를 번역하지 않습니다.

현재 에이전트 세션이 새 스킬을 찾지 못한다면 설치된 `SKILL.md`를 직접 읽고 검색을 위해 새 세션이 필요한지 설명하세요.

## 5. 첫 실제 요구 등록

설정만 요청받았다면 대시보드 URL과 `todo [requirement]` 요청 예시를 제공하세요. 작업 중인 프로젝트에 임의의 샘플 트랙을 만들지 마세요. 첫 실행을 요청받았지만 요구가 없다면 원하는 변경을 물으세요.

설치된 todo 스킬을 읽으세요. 실제 요구를 조사하고 기존 트랙과 Watch를 검색한 뒤 설치된 템플릿으로 검토 가능한 HTML 문서를 정본 상태 밖에 작성하세요. 화면에 보이는 문서와 구조화된 설명에는 선택한 언어를 사용하고, `language`와 HTML `lang`은 `en`, `ko`, `ja` 또는 `zh-CN`으로 설정하세요. ID와 스키마 키는 유지하세요.

```sh
todo-flow --state "$FLOW_STATE" register /absolute/scratch/first-track.html
```

필요하면 `--assets`를 포함하세요. `http://127.0.0.1:PORT/documents/ID/REVISION/index.html`에는 실제 반환된 ID와 리비전을 사용하세요. 등록된 문서의 브라우저 렌더링과 대표적인 상호작용을 확인하고 링크를 제공하세요. 수행하지 않은 시각적 검증을 했다고 주장하지 마세요.

todo와 watchlist 스킬은 선택적인 Jev 지원을 권장합니다. 사용할 수 있는 도구로 요청된 작업을 시작하고 마치세요. Jev가 없어도 설정이 막히지 않습니다. 요청하지 않은 선행 조건으로 설치하거나 자격 증명을 요구하지 마세요.

## 6. 선택, 실행과 인계

track-picks로 현재 상태, 의존성과 실행 가능한 범위를 살펴보세요. 사용자가 이미 이 첫 요구의 선택과 실행을 요청했다면 해당 범위에서 진행하세요. 등록이나 추천만으로 실행이 승인되지는 않습니다.

trackrun을 읽고 실제 등록된 ID를 실행하세요.

```sh
trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID
```

`--jobs`는 선택 사항입니다. `--request-only`를 사용한다면 별도 드라이버가 실행 중인지 확인하세요. 요청을 저장한 것만으로 첫 실행이 완료된 것은 아닙니다.

`state.json`, `tasks`, `attempt-records`, `results`, `decisions`, `effects`를 확인하세요. GitHub에서는 실제 Issue/PR 상태와 영수증을 비교하세요. `review` 엔드포인트는 리뷰된 후보를 만듭니다. `land` 엔드포인트는 랜딩된 SHA, 현재 유효한 분류 완료 기록, 완료된 트랙과 Issue가 있는 경우 그 종료를 요구합니다.

중단되었다면 증거를 확인하고 같은 상태에서 재개하세요. 다시 초기화하지 마세요. 실제 사용자 답변을 `answer`로 기록한 뒤 필요하면 드라이버를 실행하세요. 기술적 오류를 가상의 사용자 결정으로 만들지 마세요. 실패를 보존하고 개입을 거친 복구와 문제없이 진행된 실행을 구분하세요. 새로운 후속 TODO는 선택을 기다립니다.

선택한 언어로 **설치 및 상태 경로, 언어, 대시보드/문서 링크, 실제 결과와 Issue/PR 링크, 남은 결정과 다음 명령**을 인계하세요. 실행 중인 채로 남겨 둔 터미널/프로세스를 명시하세요.

## 기존 설치 업데이트

[UPDATES.ko.md](UPDATES.ko.md)를 읽으세요. 먼저 설치된 버전과 프로젝트 호환성을 확인하세요. 현재 주 언어, 상태 연결, 사용자 수정과 기존 실행 권한을 보존하세요. 업데이트만으로 새 트랙이나 랜딩이 승인되지는 않습니다.

uv tool 설치에서는 신뢰하는 릴리스를 명시적으로 선택하고 보호 장치가 있는 `upgrade --wheel` 경로를 사용하세요. 교체 전에 사용자/드라이버가 진행 중인 작업을 마치거나 일시 중지하게 하고 대시보드를 중지하세요. 패키지 관리자로 직접 교체하여 유지관리 충돌을 우회하지 마세요. 출력된 복구 명령과 반환된 백업 ID를 보관하세요. 설치된 프로젝트 스킬은 먼저 dry run으로 확인한 뒤 업데이트하세요. 충돌은 조정해야 하며 디렉터리 삭제나 강제 교체로 해결하지 마세요. 새 엔진, 스킬과 기존 문서를 검증한 뒤 사용자 요청에 포함된 프로세스만 다시 시작하세요.

네트워크를 통한 자동 버전 검색은 현재 제공하지 않습니다. 소스 체크아웃은 실행 중인 작업이 없을 때 기존 업데이트/설치 절차를 사용한 뒤 같은 호환성 및 스킬 검사를 수행해야 합니다. 업데이트 중단 후 CLI가 사라졌다면 교체된 환경 밖에 저장된 기본 Python 복구 실행기를 사용하세요.
