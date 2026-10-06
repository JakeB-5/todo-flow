# 기록된 품질·시간·재시도 비교

- synthetic: `true`
- 기록된 실행 근거를 같은 사례·기대값·검증 조건 그룹 안에서만 집계합니다. 모델 순위나 성능 점수가 아닙니다.
- tokens·cost는 미측정(`null`)이며 순위·가격·성능 점수는 없습니다.
- 분모: 사례는 집계한 사례 수입니다. completed와 failed는 실행 상태이고, inconclusive는 skipped와 missing_response의 합입니다. completed는 실행 상태일 뿐 모델 판단이 맞았다는 근거가 아닙니다.

## 기록

| attempt | synthetic | 비교 키 | model | effort | attempt 시간(초) | 재시도 |
| --- | --- | --- | --- | --- | --- | --- |
| attempt-a (examples/quality-run-a.json#/attempt) | true | fixture `quality-cases-v1`, 기대값 `quality-expectations-v1`, 검증 `eval-measures` | 선택 sample-model-x (selected) / 확인 sample-model-x (confirmed) | 선택 high (selected) / 확인 high (confirmed) | 300 | 3 (관측 4, 미관측 2) |
| attempt-b (examples/quality-run-b.json#/attempt) | true | fixture `quality-cases-v1`, 기대값 `quality-expectations-v1`, 검증 `eval-measures` | 선택 sample-model-y (selected) / 확인 - (unconfirmed) | 선택 medium (selected) / 확인 - (unconfirmed) | 미관측 | 미관측 (관측 0, 미관측 6) |
| attempt-c (examples/quality-run-c.json#/attempt) | true | fixture `quality-cases-v1`, 기대값 `quality-expectations-v1`, 검증 `eval-measures` | 선택 sample-model-x (selected) / 확인 sample-model-x (confirmed) | 선택 high (selected) / 확인 high (confirmed) | 300 | 2 (관측 6, 미관측 0) |
| attempt-d (examples/quality-run-d.json#/attempt) | true | fixture `quality-cases-v1`, 기대값 `quality-expectations-v2`, 검증 `eval-measures` | 선택 sample-model-y (selected) / 확인 sample-model-y (confirmed) | 선택 medium (selected) / 확인 medium (confirmed) | 120 | 1 (관측 1, 미관측 5) |

## 비교 그룹

### 그룹 1

- 비교 키: fixture `quality-cases-v1`, 기대값 `quality-expectations-v1`, 검증 `eval-measures`
- 표본 수: 3
- 분모: 사례 18, completed 9, failed 3, inconclusive 6

| model | effort | 표본 | 사례 | completed | failed | inconclusive | missed_defects | false_approvals | rework | execution_failures | 재시도 | attempt 시간 합계(초) | 벽시계 합집합(초) | attempts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sample-model-x (confirmed) | high (confirmed) | 2 | 12 | 6 | 2 | 4 (skipped 2, missing_response 2) | 2 (관측 6, 미관측 6) | 2 (관측 6, 미관측 6) | 4 (관측 6, 미관측 6) | 2 (관측 10, 미관측 2) | 5 (관측 10, 미관측 2) | 600 (관측 2, 미관측 0) | 480 | attempt-a (examples/quality-run-a.json#/attempt), attempt-c (examples/quality-run-c.json#/attempt) |
| sample-model-y (selected) | medium (selected) | 1 | 6 | 3 | 1 | 2 (skipped 1, missing_response 1) | 1 (관측 3, 미관측 3) | 1 (관측 3, 미관측 3) | 2 (관측 3, 미관측 3) | 1 (관측 5, 미관측 1) | 미관측 (관측 0, 미관측 6) | 미관측 (관측 0, 미관측 1) | 미관측 | attempt-b (examples/quality-run-b.json#/attempt) |

## 비교 불가

- attempt-d (examples/quality-run-d.json#/attempt): fixture `quality-cases-v1`, 기대값 `quality-expectations-v2`, 검증 `eval-measures`. 같은 비교 키의 다른 기록이 없습니다.
