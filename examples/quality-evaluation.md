# 합성 품질 평가 집계기

이 도구는 공개 synthetic 사례와 미리 작성한 응답으로 집계기의 정확성을 확인합니다. 실제 모델을 실행하거나 구현의 결함을 자동으로 발견하는 도구가 아닙니다. 실제 모델 품질 향상, 사람 검토 시간, token 사용량과 비용 효과는 미측정입니다. 샘플의 `unmeasured` 값은 모두 `null`입니다.

저장소 루트에서 Python 3.11 이상으로 실행합니다. 추가 패키지 설치나 네트워크 연결은 필요하지 않습니다.

```sh
PYTHONPATH=src python3 -m todo_flow.quality_eval
```

이 명령은 보고서를 표준 출력으로 내보냅니다. 기대 합계와 상태별 사례 수가 일치하면 종료 코드 0, 불일치하면 보고서와 함께 1, 잘못된 입력이면 2를 반환합니다. `matches_expected: true`는 합성 집계 검증 성공만 뜻합니다. 결함이 없거나 실제 모델 품질이 우수하다는 뜻이 아닙니다.

입력을 바꾸려면 `--cases`, `--responses`, `--expectations`로 각각 로컬 JSON 파일을 지정합니다. 기본 파일은 다음과 같습니다.

| 파일 | 역할 |
| --- | --- |
| [quality-cases.json](quality-cases.json) | 정답을 포함하지 않는 합성 입력, fixture id와 실행 순서 |
| [quality-responses.json](quality-responses.json) | 사례별 고정 응답, 검사 상태와 관측 필드 |
| [quality-expectations.json](quality-expectations.json) | 별도 채점 정답, 알려진 집계와 상태별 기대 사례 수 |
| [quality-report.json](quality-report.json) | 기본 명령으로 재현하는 synthetic 샘플 보고서 |

`FixedResponseAdapter`는 사례 id로 고정 응답만 조회하며 기대값을 받지 않습니다. prompt 문자열은 실행하지 않습니다. 모델·SDK·Engine·Git·원격 API를 호출하는 경로나 live 모드는 없습니다. 기존 `tests/fake_worker.py`의 통합 테스트 계약과 독립적인 경로입니다.

## 관측과 집계 의미

`completed`는 검사를 완료했다고 기록한 합성 응답입니다. 이 상태에서 필드가 존재할 때만 해당 품질 지표를 계산합니다. 필드 누락과 `null`은 미관측입니다. 빈 결함 목록 `[]`, `approved: false`, `rework: 0`은 명시적인 관측값입니다.

| 지표 | 관측값의 의미 |
| --- | --- |
| `missed_defects` | 정답의 결함 id 중 `found_defects`에 없는 결함 수. 결함 수를 세며 사례 수와 다릅니다. |
| `false_approvals` | 정답에 결함이 있는데 `approved: true`인 사례는 1, 명시적인 나머지 승인 판단은 0입니다. 결함 탐지 여부와 별도로 계산합니다. |
| `rework` | 응답에 기록한 합성 재작업 횟수입니다. 실제 시간이나 실행 이력에서 추정하지 않습니다. |
| `execution_failures` | 명시적인 `failed` 응답은 1, `completed`와 `skipped`는 0입니다. 응답 부재는 실패 여부도 미관측입니다. |

`skipped`는 검사 미실행입니다. `failed`는 명시적인 실행 실패입니다. 두 상태에서는 응답에 품질 필드가 있더라도 세 품질 지표를 모두 `null`로 남깁니다. 응답 항목이 없거나 `null`이면 `missing_response`로 표시하고 네 지표를 모두 `null`로 남깁니다. 정답이 정상 사례라고 해도 응답 부재를 결함 누락 0으로 바꾸지 않습니다. 검사 생략의 실행 실패 0은 검사 성공이나 품질 관측을 뜻하지 않습니다.

각 지표의 `observed_total`은 관측된 값만 더한 부분 합계입니다. `observed_cases`와 `unobserved_cases`가 적용 범위를 함께 표시합니다. 관측 사례가 하나도 없으면 합계는 `null`입니다. 관측된 0이 하나 이상 있고 나머지가 미관측이면 합계는 0이며, 미관측 사례 수는 그대로 남습니다. 미관측을 분모에 포함한 품질 성공률은 계산하지 않습니다.

기본 사례의 알려진 결과는 다음과 같습니다.

| 사례 | 상태 | 누락 결함 | 거짓 승인 | 재작업 | 실행 실패 |
| --- | --- | --- | --- | --- | --- |
| `clean` | `completed` | 0 | 0 | 0 | 0 |
| `missed` | `completed` | 1 | 1 | 0 | 0 |
| `caught` | `completed` | 0 | 0 | 2 | 0 |
| `not-run` | `skipped` | 미관측 | 미관측 | 미관측 | 0 |
| `missing-response` | `missing_response` | 미관측 | 미관측 | 미관측 | 미관측 |
| `failed` | `failed` | 미관측 | 미관측 | 미관측 | 1 |

관측 합계는 누락 결함 1, 거짓 승인 1, 재작업 2, 실행 실패 1입니다. 품질 지표는 각각 관측 3건·미관측 3건이고, 실행 실패 지표는 관측 5건·미관측 1건입니다. 이 숫자는 고정 응답에 심어 둔 결과이며 실제 A/B 측정값이 아닙니다.

## 재현과 검증

보고서는 입력 세 파일의 id·경로·원본 바이트 SHA-256, 사례 순서, 사례별 JSON Pointer 원본 참조를 보존합니다. 응답 부재의 `response_ref`는 `null`이며 응답 파일 자체는 `sources.responses`에서 확인할 수 있습니다. 응답과 기대값의 `fixture_id`는 사례 파일 id와 일치해야 합니다. 순서에는 모든 사례가 한 번씩 있어야 하고, 각 사례에 별도 정답이 있어야 합니다.

같은 파일 바이트와 경로, 순서, 기대값을 사용하면 같은 보고서 바이트가 나옵니다. 시각이나 임시 실행 id를 넣지 않습니다. 경로를 다르게 지정하면 출처 경로도 달라지므로 기본 샘플과의 바이트 비교는 저장소 루트의 기본 명령을 기준으로 합니다.

관련 테스트는 알려진 합계, 미관측과 관측된 0, 불완전한 응답, 잘못된 입력, 기대값 변경에 따른 불일치, 반복 실행과 샘플 바이트 일치, 네트워크·프로세스 호출 차단 상태의 실행을 확인합니다.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_quality_eval.py -v
```

조건 연결: 입력과 별도 정답은 `eval-cases`, 지표와 관측 범위는 `eval-measures`, 결정적 명령과 원본 참조는 `eval-replay`, 고정 응답 전용 경로는 `eval-no-effects`, synthetic 표기와 미측정 한계는 `eval-scope`에 대응합니다.

## 기록 비교 리포트

`todo_flow.quality_compare`는 명령행에 직접 지정한 로컬 실행 기록 JSON만 읽습니다. 각 기록이 가리키는 사례·응답·기대값은 `quality_eval.replay`로 다시 집계합니다. 따라서 품질 지표는 계속 synthetic 고정 응답 기준이며 실제 모델 품질 측정값이 아닙니다. 모델·네트워크·프로세스를 호출하지 않고, 시각이나 임시 id를 출력하지 않습니다.

```sh
PYTHONPATH=src python3 -m todo_flow.quality_compare --format markdown examples/quality-run-a.json examples/quality-run-b.json examples/quality-run-c.json examples/quality-run-d.json
```

기본 출력은 JSON이고 `--format markdown`은 사람이 읽는 표를 출력합니다. 위 명령의 출력은 [quality-comparison-report.md](quality-comparison-report.md)와 바이트 단위로 같습니다. 마크다운 표는 해시를 생략합니다. id가 같은데 바이트가 다른 사례·기대값은 JSON 출력의 `cases_sha256`·`expectations_sha256`으로 구분합니다.

| 기록 필드 | 의미 |
| --- | --- |
| `id`, `attempt`, `synthetic` | 기록 id, 원본 attempt id, 기록이 선언한 synthetic 표기입니다. 선언값은 JSON 출력의 `declared_synthetic`에 보존합니다. |
| `fixture_id`, `expectations.id`, `expectations.sha256` | 사례 fixture와 기대값 버전입니다. `sha256`은 선택 항목이며, 있으면 기대값 파일 바이트와 일치해야 합니다. |
| `verification` | 검증 조건 식별자 |
| `inputs` | 기록 파일 위치를 기준으로 한 `cases`, `responses`, `expectations` 경로 |
| `worker.selected`, `worker.provider_confirmed` | 선택값과 공급자 확인값의 `model`·`effort` |
| `retries` | 사례 id별로 관측된 재시도 수입니다. 없거나 `null`이면 미관측입니다. |
| `started_at`, `finished_at` | 시간대가 있는 ISO 8601 시각입니다. 하나라도 없으면 해당 attempt 시간은 미관측입니다. |

- `(fixture_id, 사례 파일 sha256, 기대값 sha256, verification)`이 같은 기록이 둘 이상일 때만 `cohorts`로 묶습니다. 사례 id가 같아도 사례 파일 바이트가 다르면 다른 사례로 봅니다. 같은 키의 다른 기록이 없으면 `non_comparable`로 나열합니다. 그룹을 넘는 순위·성공률·가격·성능 점수는 만들지 않습니다.
- 그룹 안의 행은 model·effort 값과 그 근거(`confirmed`, `selected`, `delegated`, `missing`)로 나눕니다. 근거가 `confirmed`가 되는 경우는 공급자 확인값이 있을 때뿐입니다. 선택값만 있는 기록은 `selected` 행에 남고, 확인값 행과 합쳐지지 않습니다.
- 그룹과 행마다 `denominator`로 사례 수(`cases`)와 실행 상태별 `completed`·`failed`·`inconclusive`를 표시합니다. `inconclusive`는 `skipped`와 `missing_response`의 합입니다. `completed`는 실행 상태일 뿐 모델 판단이 맞았다는 근거가 아닙니다. 상세 `status_counts`, 관측/미관측 범위가 붙은 품질 지표, 재시도 합계도 함께 표시합니다. `quality_eval`의 `matches_expected`는 집계 fixture 검증이므로 모델 성공으로 해석하지 않으며 비교 리포트에서 사용하지 않습니다.
- replay 입력은 항상 synthetic이므로 기록이 `synthetic: false`를 선언해도 행과 보고서의 `synthetic`은 `true`입니다.
- `attempt_seconds_total`은 시작·종료가 모두 있는 attempt별 소요 시간의 합입니다. 시도가 겹치면 이 값은 벽시계 시간이 아닙니다. `wall_clock_seconds`는 관측 구간의 합집합입니다. 시각이 빠진 attempt는 `unobserved_attempts`로 셉니다.
- 각 행에는 원본 attempt id와 `기록 경로#/attempt` JSON Pointer가 붙습니다. JSON 보고서는 기록 파일과 입력 파일의 경로·sha256을 보존합니다. `unmeasured`의 tokens·cost는 `null`입니다.

샘플 구성은 다음과 같습니다. a와 c는 같은 비교 키에서 확인된 같은 model·effort를 쓰며 시도 구간이 겹칩니다. 그래서 attempt 시간 합계는 600초, 합집합은 480초입니다. b는 선택값만 있고 종료 시각이 없습니다. d는 다른 기대값 버전인 [quality-expectations-v2.json](quality-expectations-v2.json)을 써서 비교 불가로 분류됩니다. 모든 model 이름은 합성 라벨이며 실제 모델 기록이 아닙니다.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_quality_compare.py -v
```

조건 연결: 비교 그룹, 분모, 비비교 분리는 `comparison-cohorts`, 선택·확인 출처와 시간·재시도 집계는 `comparison-provenance`, 결정적 출력과 미측정 유지는 `comparison-offline`에 대응합니다.
