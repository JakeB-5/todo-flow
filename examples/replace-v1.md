# replace-v1 변경 제안 계약

worker protocol 2의 경로 기반 입력과 기존 `{path,content}` 전체 파일 제안을 유지하면서, 기존 UTF-8 파일의 작은 수정에는 아래 형식을 사용할 수 있습니다. 이 문서의 예제는 합성 데이터이며 모델 속도나 오류율 개선을 실측한 결과가 아닙니다.

```json
{
  "path": "large.py",
  "format": "replace-v1",
  "base_head": "입력 context.head의 전체 Git 객체 ID",
  "sha256": "원본 파일 바이트의 소문자 SHA-256 64자리",
  "edits": [
    {"old": "value = '안녕'", "new": "value = '반가워 🌿'"}
  ]
}
```

위 HEAD·digest 설명 문자열은 실제 값으로 바꿔야 합니다. `base_head`는 40자리 또는 64자리 소문자 16진수 전체 ID입니다. 파일은 `read_bytes()`로 읽어 SHA-256을 계산합니다. 텍스트 모드의 줄바꿈 변환이나 Unicode 정규화를 거친 내용의 digest를 보내지 않습니다.

`edits`에는 하나 이상의 `{old,new}`가 필요합니다. `old`는 비어 있지 않아야 하며 원본 UTF-8 바이트에서 정확히 한 번 나타나야 합니다. 겹쳐서 나타나는 일치도 여러 일치로 계산합니다. 모든 범위는 같은 원본에서 계산하며 서로 겹치면 거부합니다. 앞선 치환 결과를 다음 `old`의 검색 대상으로 사용하지 않습니다. `new`는 빈 문자열일 수 있고, 범위 바깥의 바이트와 줄바꿈은 그대로 보존됩니다.

새 파일은 기존 `{path,content}` 형식을 사용합니다. 전체 파일 형식에는 두 필드만, replace-v1에는 위 다섯 필드만 허용합니다. 두 형식을 한 항목에 섞거나 미지원 `format`을 보내면 쓰기 전에 거부합니다. 기존 호스트가 새 형식을 읽는다고 가정하지 말고, replace-v1을 지원하는 호스트에서만 생성합니다. 입력 protocol 번호를 변경하거나 기존 상태를 이주할 필요는 없습니다.

호스트는 worker 실행 전 HEAD, claim, process barrier, 허용 경로, 경로 중복·상하위 관계, symlink, 파일 식별값, 치환 범위와 기존 사용자 변경을 검사합니다. 경로는 `./`·중복 `/`·`..` 없는 정규화된 상대 경로여야 합니다. 모든 항목의 검사가 끝나기 전에는 파일이나 index를 변경하지 않습니다. 마지막 항목에 충돌이 있어도 앞선 항목은 쓰지 않습니다. merge repair에서는 호스트가 조립한 전체 내용을 기존 해결 검사에 전달하므로 충돌 마커·누락된 해결·변경된 merge checkpoint 보호가 유지됩니다. 이 사전 검사는 프로세스 중단 시 여러 파일 쓰기의 원자성을 뜻하지 않습니다.

[replace_worker.py](replace_worker.py)는 work 작업의 JSON 입력을 stdin으로 읽고 파일을 읽은 뒤, 완전한 JSON 제안 하나만 stdout으로 반환하는 실제 command adapter 예제입니다. 호출 인자는 `path old new`이며 직접 파일을 쓰거나 Git을 실행하지 않습니다. 일반 assess/review 작업을 처리하는 범용 adapter는 아닙니다.

회귀 테스트는 예제를 실제 command worker로 실행하고 호스트 적용·commit·검증 경로를 통과시킵니다. Unicode와 CRLF를 포함한 파일의 결과 바이트 및 commit의 변경 경로를 확인하고, 파일 길이를 늘려도 동일한 한 줄 치환의 제안 바이트 수가 증가하지 않는지 비교합니다. 존재하지 않는 `old`를 보내는 충돌 예제에서는 원본·HEAD·index가 유지되어야 합니다.

```sh
uv run python -m unittest discover -s tests -p 'test_change_proposals.py' -v
uv run python -m unittest discover -s tests -p 'test_worker.py' -v
uv run python -m unittest discover -s tests -p 'test_execution_boundaries.py' -v
uv run python -m unittest discover -s tests -p 'test_integration_repair.py' -v
```

숫자로 고정된 제안 크기 제한은 계약에 포함하지 않습니다. 이번 입력·적용 경로 변경 뒤에도 `proposal-interrupt`의 잘린 응답 거부와 쓰기·commit 중단 복구 작업이 남습니다. 해당 조건을 완료하고 기존 필수 검증 및 독립 리뷰를 통과하기 전에는 트랙 완료로 채택하지 않습니다.
