# Contributing to TODO Flow

TODO Flow의 코드·스킬·템플릿과 공개 사용 문서를 함께 유지합니다. 현재 구현 범위와 제한은 [README.md](README.md), 변경 이력은 [CHANGELOG.md](CHANGELOG.md)를 기준으로 확인합니다.

## 개발 환경

Python 3.11 이상, uv, Git이 필요합니다. JavaScript 구문 검사에는 Node.js를 사용합니다. 로컬 자동 테스트는 실제 모델이나 원격 GitHub 계정 없이 실행하며 Git 통합 테스트에는 임시 로컬 저장소를 사용합니다.

```sh
uv sync --frozen
uv run todo-flow --help
```

잠긴 의존성을 사용합니다. 의존성을 변경하면 `pyproject.toml`과 `uv.lock`을 함께 갱신하고 변경 이유를 남깁니다.

## 저장소 구성

| 경로 | 내용 |
|---|---|
| `src/todo_flow/` | CLI, 파일 정본, 실행·복구, 워커·원격 어댑터, 대시보드 |
| `src/todo_flow/web/` | 대시보드 HTML·CSS·JavaScript |
| `skills/` | 등록·선정·실행·리뷰·랜딩·트리아지·Watch 스킬 |
| `templates/` | HTML·Markdown·JSON 트랙 시작 문서 |
| `tests/` | unittest 기반 단위·로컬 통합·HTTP 테스트 |
| `scripts/` | 실제 GitHub 실험과 합성 대시보드 데이터 생성 |
| `assets/metrics/` | 익명 집계·측정 방법·공개 그래프. 원자료와 출처 식별정보는 포함하지 않음 |
| `docs/` | 로컬 설계·실험 기록. Git 및 배포 제외 |

이 저장소의 `.agents/skills`는 `skills/`를 가리킵니다. 스킬 정본은 `skills/`에서 수정합니다. 다른 프로젝트로 설치할 때는 `todo-flow install-skills --target PATH`를 사용하며 같은 이름의 기존 스킬을 임의로 덮어쓰지 않습니다.

## 변경 원칙

- 등록은 `todo`, 선정은 대시보드/`track-picks`, 실행은 명시적인 `trackrun ID…` 요청으로 구분합니다. 대시보드에 문서 작성·실행 요청 기능을 추가하지 않습니다.
- 문서·실행 기록의 정본은 파일입니다. 캐시 삭제 후 복원 가능성을 유지하고 에이전트에게 SQL 검색을 필수로 요구하지 않습니다.
- 워커 판단과 호스트의 반영 경계를 유지합니다. 작업권·세대·revision·정확한 head에 대한 검증과 독립 리뷰를 우회하지 않습니다.
- 실패·중단·원격 응답 유실도 기록합니다. 실제 상태를 대조하기 전에 외부 작업을 중복 실행하거나 성공으로 간주하지 않습니다.
- 랜딩 후 트리아지 없이 완료를 채택하지 않습니다. 후속 TODO 등록은 자동이어도 실행은 사용자 선정 뒤입니다.
- HTML/Markdown 본문과 revision별 자산을 보존합니다. 문서·현재 상태·과거 revision을 검색과 조회에서 구분합니다.
- 기능 변경과 무관한 포맷 정리를 섞지 않습니다. Python에는 설정된 Ruff 규칙과 의미가 드러나는 이름을 사용합니다.

에이전트용 상세 규칙은 [AGENTS.md](AGENTS.md)에 있습니다.

## 테스트와 검사

```sh
# 전체 자동 테스트
uv run python -m unittest discover -s tests -v

# 변경에 맞는 특정 테스트 파일
uv run python -m unittest discover -s tests -p test_triage.py -v

# 정적 검사와 포맷 확인
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
node --check src/todo_flow/web/app.js

# 소스 배포본과 wheel
uv build
```

오류를 고칠 때는 관측 가능한 잘못된 동작을 재현하고 수정 후 기대 결과를 확인합니다. 실행·저장·외부 반영 변경에는 중단, 중복 요청, 늦은 응답, 재시작 사례 중 관련된 경계를 검사합니다. 단순 문구 변경에는 구현 내용을 그대로 따라가는 테스트를 추가하지 않습니다.

UI 변경은 실제 브라우저에서 대상 화면과 상호작용을 확인합니다. 합성 목록 표시, 실제 워커 동시 실행, 원격 PR 병합은 각각 별도 검증이며 서로의 증거를 대신하지 않습니다.

패키징을 바꾸면 새 체크아웃 또는 소스 배포본에서 설치·CLI 실행을 확인하고, wheel에 대시보드와 스킬 자산이 들어가는지 확인합니다. `docs/` 없이 빌드·테스트·스킬 설치가 가능해야 합니다.

## 실제 외부 실험

이 절의 명령은 실험을 명시적으로 요청받았거나 이미 승인받은 범위에서 사용합니다. 공개 저장소를 만들고 실제 모델 사용량·이슈·PR·랜딩을 발생시킵니다. 기존 승인은 존중하며, 운영 프로젝트를 임의로 실험 대상으로 바꾸지 않습니다.

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex \
  --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

새로 만든 공개 가능한 fixture만 사용합니다. 비공개 프로젝트의 소스·문서·자산·원문 로그를 공개 실험에 복사하지 않습니다. 원격 결과와 파일 기록을 대조하고, 최초 실패·개입 후 복구·수정 후 새 실행을 구분해 보고합니다.

대시보드만 확인하려면 로컬 합성 데이터를 사용할 수 있습니다. 지정한 디렉터리는 새 경로여야 합니다.

```sh
uv run python scripts/dashboard_fixture.py --state /absolute/new-dashboard-demo
uv run todo-flow --state /absolute/new-dashboard-demo serve --port 8766
```

이는 활성 48개·완료 2,500개 예시를 생성하는 읽기 전용 데모이며 실제 워커나 GitHub를 실행하지 않습니다.

## 문서와 로컬 기록

- 사용·설치·제한은 `README.md`, 변경 사항은 `CHANGELOG.md`, 기여 방법은 이 파일에 반영합니다.
- 설계 초안·실험 원문·스크린샷은 `docs/`에 로컬로 보관합니다. `/docs/`는 Git과 배포 패키지에서 제외하며 `git add -f`로 우회하지 않습니다.
- 생산성 그래프에는 집계 수치와 산식만 반영합니다. 새 측정의 원본 Git 이력·경로·저장소 주소·작성자·소스·커밋 메시지는 저장소 밖에 보관합니다. 선행 운영 기록과 현재 패키지의 벤치마크를 구분하고 관측 증가율을 인과적 효과로 표현하지 않습니다.
- 커밋할 문서나 프로그램이 로컬 `docs/` 파일에 의존하지 않게 합니다. 공유할 변경 계약은 루트 문서 또는 관련 코드·스킬에 반영합니다.
- 자격 증명, 실행 정본·원문 로그, 가상환경, 빌드 결과를 커밋하지 않습니다. 테스트 실행 상태는 저장소 밖의 전용 디렉터리에 둡니다.
- 라이선스나 공개 배포 정책을 임의로 정하지 않습니다. 현재 패키지 버전 표시는 원격 릴리스가 발행됐다는 뜻이 아닙니다.

## 커밋과 Pull Request

커밋은 변경 목적별로 나누고 명령형 제목을 사용합니다. 예: `Fix revision lookup during triage`.

PR에는 해결할 문제, 변경 후 동작, 수행한 검증과 남은 제한을 적습니다. 관련 이슈가 있다면 연결하고, UI 변경은 공개 가능한 화면 증거를 첨부합니다. 실행하지 못한 테스트는 명시합니다. 재현을 위해 비공개 로그 전체를 게시할 필요는 없습니다.

기능·사용법·호환성·중요한 오류 수정은 `CHANGELOG.md`의 `Unreleased`에 기록합니다. 실제 릴리스 시에만 버전과 날짜를 확정하고 패키지 버전·잠금 파일·변경 기록을 함께 맞춥니다.
