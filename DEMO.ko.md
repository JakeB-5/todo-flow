# TODO Flow 동작 살펴보기

[English](DEMO.md) · [한국어](DEMO.ko.md) · [日本語](DEMO.ja.md) · [简体中文](DEMO.zh-CN.md)

[README](README.ko.md) · [에이전트 설치](AGENT_INSTALL.ko.md) · [운영](OPERATIONS.ko.md) · [업데이트](UPDATES.ko.md)

<!-- translation-source: DEMO.md; source-sha256: 181597c84a147f8b0a8c41d739588656e153da14097006b5d541e294a88fe283; status: translated -->

## 이 저장소의 실제 설정

[자체 사용 안내](examples/self-hosting/README.md)는 2026년 9월 26일 이 저장소의 실제 설정을 기록합니다. 별도로 설치한 `0.0.4` 엔진, 한국어 프로젝트 언어, Codex 워커와 review 엔드포인트를 사용했습니다. 실제 빈 대시보드와 다른 환경에서도 사용할 수 있는 검증 절차가 포함되어 있습니다. 해당 시점에는 이 프로젝트에 등록하거나 실행한 트랙이 없었습니다. 이후 실제 작업 결과는 이 설정 기록과 함께 남겨야 합니다.

## 대시보드 둘러보기

![대시보드 둘러보기](assets/demo/dashboard-tour.gif)

이 화면은 **합성된 읽기 전용 데이터**를 사용한 실제 애플리케이션 캡처이며 모델 실행의 증거가 아닙니다. fixture에는 진행 중 작업, 결정 대기, 조건부 Watch를 포함해 활성 트랙 48개와 완료 트랙 2,500개가 있습니다.

[영어 화면](assets/demo/dashboard-en.png) · [한국어 화면](assets/demo/dashboard-ko.png) · [활동](assets/demo/activity-en.png) · [풍부한 계획 문서 예시](assets/demo/track-example.png)

소스 체크아웃에서 **저장소 밖의 새 디렉터리**를 사용하세요.

```sh
uv sync --frozen
uv run python scripts/dashboard_fixture.py --state /absolute/new-dashboard-demo --language en
uv run todo-flow --state /absolute/new-dashboard-demo serve --port 8766
```

`http://127.0.0.1:8766`을 엽니다.

1. 연속된 활성 목록을 스크롤하고 검색하여 실행 가능한 트랙 두 개를 선택합니다.
2. `trackrun` 명령을 복사합니다. 복사만으로 작업이 시작되지는 않습니다.
3. 활동 화면에서 담당자, 현재 작업, 결정 대기를 확인합니다.
4. 트랙을 열어 문서, 조건, 증거를 읽습니다.
5. 별도의 완료 보관함을 검색하고 필요하면 이전 기록을 불러옵니다.
6. English / 한국어 / 日本語 / 简体中文을 전환합니다. 선택과 결정 초안은 유지되며 작성된 내용은 원래 언어로 남습니다.

fixture는 대시보드의 변경 요청을 거부합니다. 실제 작업은 별도로 초기화한 프로젝트에서 실행하세요.

실행 중인 브라우저에서 화면을 캡처한 뒤 둘러보기 영상을 조합하려면 다음 명령을 사용합니다.

```sh
uv run --no-project --with Pillow==11.3.0 python scripts/render_demo.py \
  --output assets/demo/dashboard-tour.gif \
  assets/demo/dashboard-en.png assets/demo/selection-en.png \
  assets/demo/activity-en.png assets/demo/track-en.png assets/demo/completed-en.png
```

스크립트는 제공한 캡처를 조합할 뿐 실행 상태를 만들어 내지 않습니다.

## 전체 워크플로우 실행

초기 커밋, origin 원격, 인증과 정상 동작하는 테스트가 있는 일회용 프로젝트를 사용하세요. [설치 안내](AGENT_INSTALL.ko.md)에 따라 `en`, `ko`, `ja`, `zh-CN` 중 하나를 선택합니다. 실제 통합과 triage까지 포함하려면 `--endpoint land --allow-land`를 사용합니다. 그렇지 않으면 리뷰된 후보에서 멈춥니다.

에이전트에게 범위가 제한된 두 요구를 전달합니다. 예를 들면 다음과 같습니다.

```text
todo 일시적인 네트워크 오류에 제한된 재시도를 추가해줘. 영구적인 오류 처리 동작은 유지하고 테스트를 추가해줘.
todo 영구적인 요청 실패를 유용한 다음 조치와 함께 설명해줘. 메시지 테스트를 추가해줘.
trackpicks
```

[재시도 계획](examples/retry-backoff.html)과 [오류 메시지 계획](examples/request-error-message.html)은 검토 가능한 HTML 문서 예시입니다. 등록 전에 실제 fixture에 맞게 범위와 증거를 조정하세요. 완료된 작업이 아닌 예시입니다.

생성된 문서를 검토한 뒤 실제 반환된 ID로 실행을 요청합니다.

```sh
trackrun retry-backoff request-error-message
```

대시보드에서 별도 워크트리와 작업을 관찰하세요. 각 후보의 실제 검증과 독립 리뷰를 확인합니다. 랜딩이 허용됐다면 통합 SHA, 랜딩 후 triage, 이슈 종료와 완료를 확인합니다. 결정 대기 중이면 답하고 필요할 때 드라이버를 다시 시작합니다. 새 후속 TODO는 선정되지 않은 상태로 남습니다.

## 이전 공개 수용 시험 살펴보기

2026년 9월 24일 일회용 공개 시험에서 다음 결과를 만들었습니다.

| 요구 | 이슈 | 병합된 변경 |
|---|---|---|
| 텍스트 slug 변환 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/1) | [PR #3](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/3) |
| 시퀀스 묶음 나누기 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/2) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/4) |
| 숫자 범위 제한 | [Issue #5](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/5) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/6) |

처음 두 트랙은 triage 중복 검색 결함을 수정한 뒤 복구가 필요했습니다. 새로 실행한 세 번째 트랙은 개입 없이 완료됐습니다. 이 산출물은 당시의 실행 워크플로우를 보여주며 이후의 모든 UI·다국어 변경이나 대규모 프로젝트 성능을 입증하지 않습니다.

## 원격 수용 시험 재현

최근 개발 실행은 2026년 9월 24일 눈에 보이는 Orca 터미널에서 경로 기반 워커를 사용했습니다.

| 요구 | 이슈 | 병합된 변경 |
|---|---|---|
| 연속 공백 축약 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/1) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/4) |
| 서로 다른 값의 첫 등장 보존 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/2) | [PR #5](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/5) |
| 명시적 대체 동작을 가진 나눗셈 | [Issue #3](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/3) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/6) |

구현 워커 세 개가 동시에 실행됐습니다. 기준 브랜치 변경 후 반복한 triage를 포함해 실제 Codex 워커 14개가 Orca 터미널에서 실행됐습니다. 선정한 세 트랙은 모두 완료됐고, 새 사용 문서 TODO와 기존 라이선스 TODO는 선정되지 않은 상태로 남았습니다. 결정 대기나 런타임 오류는 없었으며 전달한 fixture는 테스트 19개를 통과했습니다. fixture에는 150 KB보다 큰 소스 파일이 포함됐습니다. 이는 범위가 제한된 수용 시험이며 대규모 프로젝트 벤치마크나 실제 Claude 검증이 아닙니다.

완료된 실행의 워크트리 11개와 워커 터미널 14개를 제거하면서 기존 증거 파일 466개와 모든 로컬 브랜치의 끝 커밋을 보존했습니다. 이어진 [정리 수명주기 작업(PR #8)](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/8)은 실제 Codex 워커로 완료됐습니다. 랜딩과 triage 후 해당 작업의 워크트리 3개와 워커 터미널 4개가 자동 제거되어 주 체크아웃과 보존된 증거만 남았습니다. 워크플로우가 [Issue #7](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/7)을 종료했습니다.

다음 명령은 **공개 저장소와 실제 이슈·PR·모델 호출·병합을 생성합니다**. 해당 외부 실험을 의도할 때만 본인 계정과 새 시험 디렉터리로 실행하세요.

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex --launcher orca --register-orca --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

이 방식은 실행 중인 로컬 Orca 앱에도 일회용 fixture를 등록하며 실제 터미널이 필요합니다. 화면 없는 실행에는 `--launcher headless`를 사용하고 `--register-orca`를 제외하세요. 스크립트는 검토 가능한 HTML 계획을 등록합니다. 결과 보고서에는 실제 트랙, 원격 산출물, 워커 실행의 시간 중첩, 터미널 영수증, 입력 크기가 기록됩니다. 최초 실패, 복구, 새 실행 결과는 구분하여 보존하세요. 실험 범위는 [CONTRIBUTING](CONTRIBUTING.md#demos-and-external-acceptance)을 참고하세요.
