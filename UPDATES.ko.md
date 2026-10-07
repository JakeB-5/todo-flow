# TODO Flow 업데이트

[English](UPDATES.md) · [한국어](UPDATES.ko.md) · [日本語](UPDATES.ja.md) · [简体中文](UPDATES.zh-CN.md)

[README](README.ko.md) · [에이전트 설치](AGENT_INSTALL.ko.md) · [운영](OPERATIONS.ko.md) · [데모](DEMO.ko.md)

<!-- translation-source: UPDATES.md; source-sha256: e0039bd400632ee32c96480283835c0299cdb8669c58e31925ead1e79af4c06c; status: translated -->

공유 엔진을 한 번 업데이트한 뒤 각 프로젝트에 설치된 스킬을 업데이트합니다. 프로젝트 문서와 실행 기록은 기존 상태 디렉터리에 남습니다. 업데이트는 프로젝트를 다시 초기화하거나 대기 중인 작업을 시작하지 않습니다.

<a id="update-capabilities"></a>

## 업데이트 기능

| 기능 | 동작 |
|---|---|
| 버전 표시 | `todo-flow --version`과 `trackrun --version`은 설치된 패키지 버전을 표시합니다. |
| 버전 진단 | `diagnose-versions`는 실행 중인 엔진, PATH의 `todo-flow`/`trackrun`, 설치된 스킬 매니페스트, 루프백 대시보드를 별도의 출처로 보고하고, 제공된 wheel의 업데이트 계획을 아무것도 바꾸지 않고 설명합니다. |
| 호환성 확인 | `compatibility`는 엔진, 상태/설정 형식, 워커 프로토콜과 선택적으로 설치된 스킬의 차이를 보고합니다. |
| 엔진 업데이트 | `upgrade --wheel`은 명시적으로 제공한 더 최신의 로컬 릴리스 wheel로 uv tool 설치를 교체합니다. |
| 런타임 상호 배제 | 협력하는 CLI 작업, 드라이버, 대시보드는 프로세스 잠금을 유지합니다. 엔진 업데이트에는 이들 모두의 중지가, 스킬 업데이트에는 해당 프로젝트의 유휴 상태가 필요합니다. |
| 스킬 업데이트 | 설치 매니페스트는 버전, 프로토콜, 파일의 SHA-256 기준값을 기록합니다. 업데이트는 기준값, 현재 파일, 새 번들을 비교합니다. |
| 로컬 편집 | 사용자 파일, 프로젝트 언어/상태 바인딩, 새 번들에서 바뀌지 않은 파일에 대한 편집을 보존합니다. 변경이 충돌하면 어떤 수정도 하기 전에 전체 업데이트를 중단합니다. |
| 백업과 롤백 | 엔진 환경/진입점과 영향을 받는 스킬 디렉터리를 교체 전에 백업합니다. 롤백은 명시적으로 실행하며, 업데이트 실패 시 변경 전 사본을 복원합니다. |
| 중단된 업데이트 | 영속적인 pending 표시가 미완료 엔진 업데이트의 정상 사용을 막습니다. 복구 실행기는 교체되는 환경 밖에 남습니다. 스킬 업데이트에는 별도의 복구 표시가 있습니다. |
| 이후 버전과의 호환성 | 알 수 없는 상태/설정 형식과 워커/스킬 프로토콜을 거부합니다. 이전 리더는 미래 형식의 대기 중 상태 저널을 적용하지 않습니다. |

`0.0.1` 릴리스의 계약은 **상태 형식 1, 설정 형식 1, 워커 프로토콜 1, 스킬 프로토콜 1**입니다. 패키지 버전과 데이터 형식은 독립적입니다. 새로운 선택적 설정 메타데이터가 없는 기존 파일 기반 프로젝트는 형식/프로토콜 1을 사용합니다. 이 릴리스에는 데이터 마이그레이션이 필요하지 않습니다. 기존 SQL→파일 마이그레이션은 별도의 명시적 명령으로 유지됩니다.

`0.0.2`는 경로 기반 입력을 위한 **워커 프로토콜 2**로 초기화합니다. 새로 초기화한 프로젝트 설정은 엔진 `0.0.2` 이상을 요구합니다. 상태/설정/스킬 형식은 1로 유지됩니다. 내장 Claude/Codex 어댑터는 기존 프로젝트 설정을 받아들이고 상태를 다시 쓰지 않고 새 경로 기반 입력을 생성합니다. 사용자 정의 명령 어댑터는 `workspace`와 `paths`를 읽도록 수정한 뒤 중지 상태에서 명시적으로 프로토콜 2를 선택해야 합니다. 프로토콜 1 사용자 정의 워커는 시작 전에 실패합니다. 이전 릴리스 엔진은 새로 초기화한 프로토콜 2 프로젝트를 실행할 수 없습니다. 터미널 선택은 별도의 선택적 설정입니다(`worker_launcher`, 기본값 `auto`). `--launcher`는 현재 드라이버만 바꿉니다. [워커 실행](OPERATIONS.ko.md#worker-context-and-terminal-launchers)을 참고하세요.

`0.0.3`은 형식이나 프로토콜을 바꾸지 않고 통합 복구 인계와 재개를 수정합니다. 상태 마이그레이션은 필요하지 않습니다. 복구 지침은 엔진에 포함됩니다. 일반 업데이트 절차로 각 프로젝트에 설치된 스킬을 확인하세요.

`0.0.4`는 형식이나 프로토콜을 바꾸지 않고 커밋, 리뷰, 검증 프로세스의 경계 검사를 추가합니다. 상태 마이그레이션은 필요하지 않습니다. 새 체크아웃/인덱스 체크포인트 없이 이전에 중단된 병합은 알 수 없는 스테이징 변경을 채택하지 않고 점검을 위해 보존합니다. 엔진을 전환하기 전에 기존 복구를 마치거나 점검하세요. [실행 경계](OPERATIONS.ko.md#review-landing-and-completion)를 참고하세요.

`0.0.5`는 선언된 검증 입력의 식별 정보, 영속적인 프로세스 정리, 제한된 터미널 수명주기와 지원되는 native Orca/Codex 세션을 추가합니다. 상태/설정 형식과 워커/스킬 프로토콜은 바뀌지 않습니다. 식별 정보가 없는 기존 검증 성공에는 새 검증이 필요합니다. `init --verify-identity`는 새 상태에 적용되며 업그레이드가 기존 설정을 다시 쓰지 않습니다. 범위에 근거한 계획·작업·리뷰 지침을 받으려면 프로젝트 스킬을 업데이트하세요. 이 버전의 native 세션은 Codex CLI 0.157.1을 지원합니다. 호환 경로와 로컬 검증의 한계는 [운영 문서](OPERATIONS.ko.md#native-orca-worker-sessions)에 설명되어 있습니다.

`0.0.6`은 정확한 Codex 버전 제한, 자격 증명 파일 제한, 터미널 수에 따른 시작 제한을 제거합니다. Native 워커는 기존 Codex 로그인을 재사용합니다. 이전 터미널 기록과 용량 장부는 과거 증거로 남으며 다음 워커 시작 전에 마이그레이션할 필요가 없습니다. 완료된 제안은 뷰어 정리가 연기되어도 보존됩니다. 상태/설정과 워커/스킬 프로토콜 버전은 바뀌지 않습니다. 수정된 실행 지침을 받으려면 프로젝트 스킬을 업데이트하세요.

`0.0.7`은 기본 워커 시간제한을 제거하고, 호스트가 관측하는 native 사이드바 상태와 완료 이력 대조, 실행 중 취소와 검증된 소유 작업 트리 정리를 제공합니다. 기존의 명시적 워커 제한은 유지됩니다. `worker_timeout: null`은 무제한 실행을 선택합니다. 상태/설정 형식과 워커/스킬 프로토콜은 바뀌지 않습니다. 요청 범위, 비례적인 검증, 자동 정리 지침을 받으려면 프로젝트 스킬을 업데이트하세요.

`0.0.8`은 트랙별 활동 요약, 부모 출처를 포함한 정확한 대기 의무 중복 제거, 크기를 제한한 교체 제안과 보존되는 검증 로그 아티팩트를 추가합니다. 상태/설정 형식과 워커/스킬 프로토콜은 바뀌지 않습니다. 제한된 변경 제안 지침을 받으려면 프로젝트 스킬을 업데이트하세요.

`0.0.9`는 선택적인 검증 사전 검사와 중간 검사, 조건에 연결된 기계 증거, 워커 중지 영수증, 명시적인 요청 시도 횟수 제한과 오래된 통합 체크아웃 정리를 추가합니다. 상태/설정 형식과 워커/스킬 프로토콜은 바뀌지 않습니다. 기존 프로젝트 설정은 다시 쓰지 않습니다. 사전 검사와 관련 검사는 새 상태에서 선택하는 설정입니다. 단계별 검증 지침과 트랙 실행 완료 보고 전에 소유 리소스 정리를 마쳐야 한다는 요구를 받으려면 프로젝트 스킬을 업데이트하세요.

`0.1.0`은 4개 언어의 안내서·UI, 테마 선택, 워커 모델·에포트 근거, 역할별 라우팅, 검증 결과별 후속 처리, 독립 리뷰 출처 구분, 스킬 공존과 오프라인 품질 사례를 추가합니다. 이전 실행이 트랙 잠금을 보유한 동안 후속 작업이 점유하지 못하게 합니다. 상태·설정 형식과 워커·스킬 프로토콜은 그대로이며 데이터 마이그레이션은 필요 없습니다. 유휴 상태에서 엔진과 프로젝트 스킬을 업데이트하세요. 기존 프로젝트 바인딩·로컬 수정·설정은 보호됩니다.

`0.1.1`은 새 병합에서 완료된 무변경 제안을 재사용하는 오류를 수정하고, 커밋 참조를 보존하면서 이전의 깨끗한 통합 체크아웃을 안전하게 정리하며, 테스트 런타임 등록을 실제 설치와 격리합니다. 상태·설정 형식과 워커·스킬 프로토콜은 그대로이며 데이터 마이그레이션은 필요 없습니다. 유휴 상태에서 엔진을 업데이트하세요. 기존 프로젝트 상태·사용자 변경·확인되지 않은 자원은 계속 보호됩니다.

<a id="1-inspect-and-stop-relevant-processes"></a>

## 1. 관련 프로세스 확인 및 중지

```sh
todo-flow --version
trackrun --version
todo-flow --state /absolute/project/todo compatibility \
  --target /absolute/project/.agents/skills
```

무엇이든 중지하기 전에 실제로 사용 중인 버전을 확인하려면 읽기 전용 진단을 실행하세요. 실행 중인 각 대시보드 URL과, 선택적으로 설치할 로컬 릴리스 wheel을 전달합니다:

```sh
todo-flow --state /absolute/project/todo diagnose-versions \
  --target /absolute/project/.agents/skills \
  --dashboard http://127.0.0.1:8765 \
  --wheel /absolute/releases/todo_flow-0.1.1-py3-none-any.whl
```

`diagnose-versions`는 런타임 잠금을 잡지 않고, `TODO_FLOW_HOME`을 만들지 않으며, PATH 실행 파일을 실행하지 않고(인터프리터 환경의 패키지 메타데이터를 읽습니다), 토큰이 필요 없는 `/api/version`을 통해 `127.0.0.1`/`localhost` 대시보드에만 질의합니다. 각 항목은 `source`, `path`, `version`, `status`(`observed`, `unknown`, `unreachable`)를 보고합니다. `/api/version`이 없는 대시보드는 unknown으로 보고되며 이전 릴리스일 수 있습니다. `--wheel`을 지정하면 계획은 `applicable`, `blocked`(응답하는 대시보드, 실행 중인 작업, 대기 중인 트랜잭션 또는 중단된 업데이트), `conflict`(설치된 스킬의 편집이 wheel에 포함된 스킬과 충돌), `unknown`(불분명한 릴리스 매니페스트 또는 uv tool 설치가 아닌 엔진) 중 하나이고, 그 뒤에 실행할 명령이 이어집니다. wheel 검사, 프로젝트 호환성, 스킬 dry-run 검사를 재사용하며, 더 높은 버전 번호만으로 설치 가능하다고 판단하지 않습니다. 구조화된 출력이 필요하면 `--json`을 추가하세요.

작업을 끝내거나 일시 중지한 다음 드라이버와 대시보드를 정상적으로 종료하세요. 업데이터가 워커를 대신 종료하지는 않습니다. 해결되지 않은 실행 중 작업과 기록된 살아 있는 워커 PID도 업데이트를 막습니다. 이들을 점검하고, 중지한 드라이버의 claim이 만료된 뒤 `reconcile`을 사용하세요. 대기 중 요청은 보존되며 업데이트로 실행되지 않습니다.

정상 사용 중 프로젝트 상태 경로는 `TODO_FLOW_HOME` 아래에 등록됩니다. 기본값은 `$XDG_STATE_HOME/todo-flow` 또는 `~/.local/state/todo-flow`입니다. 엔진 업데이터는 알려진 프로젝트를 후보의 호환성 매니페스트와 비교합니다. 이 런타임으로 사용한 적 없는 프로젝트는 자동으로 발견할 수 없으므로 버전을 바꾸기 전에 명시적으로 확인하세요.

협력하는 모든 프로세스는 같은 `TODO_FLOW_HOME`을 사용해야 합니다. 프로세스 잠금은 조정 경계이며 샌드박스가 아닙니다. 보호 장치가 없는 이전 프로세스, 직접 실행한 패키지 관리자 명령, 소스 편집, 사용자 정의 통합은 잠금을 우회할 수 있으므로 업데이트 전에 해당 프로세스를 중지하세요. Windows와 분산 파일시스템은 현재 지원 범위 밖입니다.

<a id="2-update-the-shared-engine"></a>

## 2. 공유 엔진 업데이트

이 경로에는 기존 **`uv tool install` 설치**와 PATH에서 찾을 수 있는 `uv`가 필요합니다. 소스 체크아웃, editable 환경, 일반 가상환경 설치, 사용자 정의 추가 요구사항/옵션 또는 진입점을 가진 uv tool 설치는 덮어쓰지 않고 진단합니다. 해당 환경이 유휴 상태일 때 원래의 설치 방식으로 업데이트한 다음 프로젝트 호환성과 스킬을 확인하세요.

[v0.1.1 릴리스](https://github.com/JakeB-5/todo-flow/releases/tag/v0.1.1)에서 wheel과 `SHA256SUMS`를 다운로드하고 체크섬(attestation이 있는 릴리스는 [출처](#verify-release-provenance)도)을 확인한 뒤 로컬 wheel 경로를 전달하세요.

```sh
todo-flow upgrade --wheel /absolute/releases/todo_flow-0.1.1-py3-none-any.whl --dry-run
todo-flow upgrade --wheel /absolute/releases/todo_flow-0.1.1-py3-none-any.whl
```

계획에는 버전, 아티팩트 해시, 호환성 계약과 알려진 프로젝트가 표시됩니다. 실행 시 배타적 런타임 잠금 아래에서 이를 재확인하고 아티팩트 사본을 보관하며, 설치 환경과 두 진입점을 백업한 뒤 uv를 호출하고 설치된 버전·진입점·포함된 스킬을 검사합니다. 일반적인 설치/검증 실패는 이전 환경을 복원합니다. 업그레이드는 프로젝트 설정, 문서, claim 또는 원격 상태를 변경하지 않습니다.

반환된 `backup`은 엔진 영수증을 식별합니다. 나중에 이를 복원하려면 다음을 실행하세요.

```sh
todo-flow upgrade --rollback ENGINE_BACKUP_ID
```

롤백은 예상한 현재 릴리스를 요구하며 이전 엔진이 알려진 프로젝트 데이터를 여전히 읽을 수 있는지 확인합니다. 코드 합입이나 원격 효과를 되돌리지 않으며 프로젝트 상태도 롤백하지 않습니다. 엔진 롤백과 프로젝트 스킬 롤백은 별도 작업입니다.

<a id="verify-release-provenance"></a>

### 릴리스 출처 확인

`v0.1.1` 이후 첫 릴리스부터, 태그로 실행되는 [릴리스 워크플로](.github/workflows/release.yml)가 게시한 릴리스에는 wheel과 소스 압축본에 대한 GitHub artifact attestation(SLSA 빌드 출처)이 포함됩니다. `v0.1.1`과 그 이전 릴리스는 attestation 없이 게시되었으므로 `SHA256SUMS`로만 확인하세요. 이들에 대해서는 `gh attestation verify`가 실패합니다.

먼저 체크섬을 확인한 다음 GitHub CLI(`gh`, 아래 플래그는 2.88.1에서 확인)로 다운로드한 각 파일을 검증하세요. `X.Y.Z`는 릴리스 버전으로 바꿉니다.

```sh
cd /absolute/releases
shasum -a 256 -c SHA256SUMS
gh attestation verify todo_flow-X.Y.Z-py3-none-any.whl \
  --repo JakeB-5/todo-flow \
  --signer-workflow JakeB-5/todo-flow/.github/workflows/release.yml \
  --source-ref refs/tags/vX.Y.Z \
  --deny-self-hosted-runners
gh attestation verify todo_flow-X.Y.Z.tar.gz \
  --repo JakeB-5/todo-flow \
  --signer-workflow JakeB-5/todo-flow/.github/workflows/release.yml \
  --source-ref refs/tags/vX.Y.Z \
  --deny-self-hosted-runners
```

모든 명령이 성공했을 때만 설치하세요. `--repo`, `--signer-workflow`, `--source-ref`는 모두 이 정책의 필수 요소입니다. 이를 빼거나 `--owner` 등으로 완화하지 마세요. digest가 일치한다는 사실만으로는 배포자를 식별할 수 없습니다. 이 정책에서는 다음 경우 검증이 실패합니다.

- 바이트가 바뀐 파일: 파일 digest가 어떤 attestation subject와도 일치하지 않습니다.
- 다른 저장소의 attestation(`--repo`)
- 이 저장소의 다른 워크플로 파일을 포함한, 다른 워크플로의 attestation(`--signer-workflow`)
- 브랜치나 다른 태그 등 다른 ref에서 만든 attestation(`--source-ref`)
- self-hosted runner에서 만든 attestation(`--deny-self-hosted-runners`)

릴리스 노트에는 워크플로가 빌드한 소스 커밋이 적혀 있습니다. 승인된 소스 커밋이 지정되어 있으면 두 명령 모두에 `--source-digest COMMIT_SHA`를 추가하고, 그 커밋을 별도로 얻은 값(예: 자신의 클론에서 `git rev-parse vX.Y.Z^{commit}`)과 비교하세요. 릴리스 노트는 같은 워크플로가 작성합니다.

신뢰 판단은 검증된 서명 인증서(저장소, 워크플로, ref, 커밋)에 근거하고, `--format json` 출력의 `statement.predicate` 필드는 근거로 쓰지 마세요. attestation을 만든 워크플로가 그 predicate 필드를 제어할 수 있습니다. attestation은 파일이 어디서 어떻게 빌드되었는지를 기록할 뿐이며 소프트웨어가 안전하거나 결함이 없음을 보여주지 않습니다. 이 공개 저장소의 attestation은 공개 Sigstore transparency log에도 기록되며, 각 릴리스 빌드의 저장소, 워크플로, ref, 커밋이 공개됩니다.

<a id="3-update-each-projects-installed-skills"></a>

## 3. 각 프로젝트에 설치된 스킬 업데이트

Claude에는 `.claude/skills`를, 해당 프로젝트 설치에는 `.agents/skills`를 사용하세요.

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --dry-run

todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills
```

`--state`를 생략하면 설치된 `project.json`에서 상태 경로를 제공할 수 있습니다. 기존 바인딩과 그 정확한 내용을 보존합니다. 관리 번들 밖의 추가 프로젝트 스킬은 건드리지 않습니다. 제거된 번들 리소스는 여전히 원래 기준값과 일치할 때만 삭제합니다. 폐기된 스킬 디렉터리의 사용자 파일은 남습니다.

사용자와 릴리스가 같은 관리 파일을 모두 변경했다면 명령은 충돌을 나열하고 아무것도 적용하지 않습니다. 다시 시도하기 전에 사용자의 편집을 새 번들과 조정하세요. 일괄 강제 덮어쓰기 옵션은 없습니다. 업데이트한 스킬 지침을 발견하도록 필요하면 새 에이전트 세션을 시작하세요.

완료된 업데이트를 복원하려면 다음을 실행하세요.

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --rollback SKILL_BACKUP_ID
```

업데이트 뒤 파일이 바뀌었다면 롤백을 거부하므로 이후 사용자 편집을 조용히 지울 수 없습니다. 먼저 해당 변경을 보존하고 조정하세요.

<a id="installations-created-before-manifests"></a>

### 매니페스트 도입 전에 생성한 설치

기존의 추적되지 않은 스킬 디렉터리를 수정 없는 원본으로 추정하지 않습니다. **일치하는 원래 번들**로 다음을 실행하세요.

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --adopt --dry-run

todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --adopt
```

채택하려면 이전 관리 파일이 해당 번들과 정확히 일치해야 합니다. 다르면 설치를 삭제하지 말고 원래 버전을 확인하여 파일을 조정하세요. 채택 후에는 이후 업데이트에서 신뢰할 수 있는 기준값을 사용합니다.

<a id="4-recover-an-interrupted-update"></a>

## 4. 중단된 업데이트 복구

```sh
# 설치된 CLI가 아직 시작되는 경우:
todo-flow upgrade --recover

# 프로젝트 스킬의 경우:
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --recover
```

엔진 교체 전에 명령은 절대 경로로 된 **기본 Python + 복구 실행기 명령**을 출력합니다. 그 줄을 보관하세요. 실행기는 교체되는 환경 밖의 `TODO_FLOW_HOME/engine-updates/ID/engine_updates.py`에 있습니다. 업데이트 도중 CLI 진입점이 사라졌다면 출력된 명령에 `--recover`를 붙여 실행하세요. 영속적인 영수증을 사용해 이전 설치를 복원하며 실행 중인 대시보드나 모델은 필요하지 않습니다.

영수증, 변경 불가능한 이전 사본과 교체되어 옮겨진 디렉터리는 점검용으로 보존합니다. 로컬 경로와 사용자 정의 스킬 내용이 포함될 수 있으므로 Git과 공개 보고에 넣지 마세요. 자동 보존 기간 정리는 아직 구현되지 않았습니다. 복구를 우회하려고 pending 표시를 수동으로 제거하지 마세요.

<a id="verification"></a>

## 검증

단위 테스트는 충돌, 사용자 편집 보존, 폐기된 리소스, 정확한 롤백, 부분 실패, 복구, 심볼릭 링크 거부, 런타임 상호 배제와 미지원 형식을 검사합니다. 격리된 인수 테스트 스크립트는 합성 미래 릴리스를 빌드하고 일반 설치를 건드리지 않고 실제 uv tool 교체를 시험합니다.

```sh
uv build --out-dir dist/update-check
uv run python scripts/update_smoke.py --artifacts dist/update-check --root /absolute/new-update-test-directory
```

실행 중인 대시보드의 업그레이드 차단, 성공적인 엔진 업데이트, 스킬 업데이트/롤백, 엔진 롤백, 의도적으로 망가뜨린 릴리스로부터의 자동 복구와 CLI가 없는 경우의 복구를 검사합니다. 모든 정본 상태 파일의 전후를 비교하고 대기 작업이 실행 없이 보존되었는지도 확인합니다. 모델 호출, Issue, PR 또는 원격 변경은 필요하지 않습니다. 합성 미래 wheel은 테스트 아티팩트이며 게시할 릴리스가 아닙니다.

<a id="further-preparation-for-future-releases"></a>

## 향후 릴리스를 위한 추가 준비

다음은 후속 항목이며 현재 구현이 이미 제공하는 기능이 아닙니다.

| 우선순위 | 준비 | 이유 / 완료 기준 |
|---|---|---|
| 공개 릴리스 전 | 공식 배포 채널과 패키지/저장소 이름 선택 | 하나의 정식 설치 URL과 업그레이드 출처를 공개하고, 패키지 인덱스 설치를 안내하기 전에 이름 소유권을 확인합니다. |
| 공개 릴리스 전 | 불변 릴리스 버전과 재현 가능한 빌드 | 릴리스 워크플로는 태그 커밋을 빌드하고, 이미 있는 릴리스를 거부하며, 게시한 wheel과 소스 압축본에 attestation을 만듭니다([릴리스 출처 확인](#verify-release-provenance) 참고). 저장소 차원에서 강제하는 태그/릴리스 불변성과 바이트 단위로 재현 가능한 빌드는 아직 갖추지 않았습니다. |
| 공개 릴리스 전 | 지원되는 호스팅 실행기에서 업데이트 인수 작업 실행 | 로컬 성공은 설정된 Linux/macOS 매트릭스를 대체하지 않습니다. |
| 데이터 형식 변경 전 | 사전 검사, 상태 백업, 재개 가능한 체크포인트, 다운그레이드 규칙을 갖춘 명시적 마이그레이션 레지스트리 | 실제 이전→새 형식 변환을 정의하고 출시 전에 중단을 시험합니다. 패키지 버전만으로 마이그레이션을 추정하지 않습니다. |
| 다음 | 버전 탐색과 stable/preview 채널 | 사용 가능한 릴리스와 변경을 보여주고 업데이트 잠금을 얻기 전에 정확한 아티팩트를 결정합니다. |
| 다음 | 패키지 인덱스로의 신뢰할 수 있는 게시 | GitHub Release 파일에는 빌드 출처 attestation이 있습니다. 신뢰할 수 있는 게시(trusted publishing)를 사용한 패키지 인덱스 게시는 설정되어 있지 않습니다. |
| 다음 | 의존성/Python 호환성과 롤백 검증 | 같은 계약의 패키지 버전뿐 아니라 실제 의존성 변경과 인터프리터 전환을 시험합니다. |
| 다음 | 프로젝트 목록 관리와 일괄 업데이트 | 알려진 프로젝트를 나열·제외·이동하고 폴더로 추정하는 대신 프로젝트별 계획과 영수증을 제공합니다. |
| 다음 | 프로젝트/스킬 프로토콜 마이그레이션과 혼합 버전 지원 정책 | 이전 설치 스킬과 워커가 각 엔진과 호환되는 기간을 정의합니다. |
| 다음 | 정상적인 작업 소진과 더 강한 워커 프로세스 식별 | 장시간 업데이트 일정을 개선하고 프로세스 시작 식별 정보로 기록된 PID와 PID 재사용을 구별합니다. |
| 다음 | 백업 보존 기간, 디스크 공간 검사, 중단된 백업 정리 | 로컬 저장 공간이 무한히 늘지 않으면서 복구 가능성을 유지합니다. |
| 이후 | 오프라인 릴리스 번들, 프록시/인덱스 설정, 조직 배포 제어 | 의존성을 자유롭게 가져올 수 없는 환경에서 설치를 재현 가능하게 합니다. |
| 이후 | 추가 운영체제 지원 | Windows 지원을 주장하기 전에 POSIX 전용 잠금/프로세스 가정을 교체합니다. |

자동 최신 버전 다운로드, 상태 형식 마이그레이션, 예약된 자체 업데이트, 플러그인 마켓플레이스 업데이트와 프로젝트 간 전체 성공/전체 실패 방식의 업그레이드는 이 릴리스에 구현되지 않았습니다.
