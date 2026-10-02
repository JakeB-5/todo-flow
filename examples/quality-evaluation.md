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
