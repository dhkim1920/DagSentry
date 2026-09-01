# Core 수명 주기 계약

DagSentry는 명시적인 상태 Enum을 영속화하고, 상태 변경은 소유 도메인 연산으로만 허용합니다. Failure Event,
Diagnosis, Error Signature는 불변 사실이며 Diagnosis Outbox만 재시도 수명 주기를 가집니다.

## Failure Event

`FailureState`는 실패한 한 Try에서 관찰한 Airflow 상태를 기록합니다.

- `UP_FOR_RETRY`: 재시도 callback 또는 Reconciler에서 수용합니다.
- `FAILED`: 최종 실패 Listener 또는 Reconciler에서 수용합니다.

소스/상태 검증기는 재시도 callback의 `FAILED`와 Listener의 `UP_FOR_RETRY`를 거부합니다. 삽입한
Failure Event의 상태는 바뀌지 않습니다. 이후 Try는 고유한 `try_number`, `event_key`로 식별되는 별도
Failure Event입니다.

## Diagnosis Outbox

`OutboxStatus`의 허용 전이는 다음과 같습니다.

```text
insert -> PENDING
PENDING -> PROCESSING
PROCESSING -> COMPLETED
PROCESSING -> PENDING       남은 시도가 있는 재시도 가능 실패
PROCESSING -> PROCESSING    새 Worker가 오래된 lock을 회수
PROCESSING -> DEAD          영구 실패 또는 시도 소진
DEAD -> PENDING             명시적인 운영자 requeue만 허용
```

`COMPLETED`는 종료 상태입니다. 마지막 시도에서 오래된 `PROCESSING` 행은 `DEAD`가 됩니다. 소유자,
lock, 시도 조건은 Worker가 더 이상 소유하지 않은 job을 완료하지 못하게 합니다. 수동 requeue는 `DEAD`에만
허용되고 이전 lock/오류 데이터를 지운 뒤 새로운 제한 시도 주기를 시작합니다.

## Diagnosis와 Error Signature

`DiagnosisSource`는 `RULE`, `AI`, `REUSED`이고 `DiagnosisValidationStatus`는 `PASSED` 또는
`REJECTED`입니다. Rule Diagnosis는 결정적으로 `PASSED`로 저장됩니다. 스키마와 Evidence가 유효한 AI
출력은 `AI/PASSED`, 거부된 AI 출력은 `AI/REJECTED`로 저장하며 유효 Rule fallback은 별도 행입니다.
재사용 결과는 호환되는 원본을 참조하는 `REUSED/PASSED`입니다. Diagnosis 행은 삽입 후 전이하지 않으며,
후속 시도나 fallback은 새 행을 만들어 이력을 보존합니다.

`SignatureStatus`는 `SIGNABLE` 또는 `UNSIGNABLE`입니다. `SIGNABLE`은 정규 데이터와 fingerprint를
요구하며 멱등적으로 저장할 수 있습니다. `UNSIGNABLE`은 정규/fingerprint 필드가 모두 없어야 하고
Signature 행을 만들지 않습니다. fingerprint 규칙 변경은 새 `fingerprint_version`을 만들며 기존
Signature의 상태나 식별자는 바뀌지 않습니다.

## 검증

```shell
uv run pytest tests/test_failure_event.py tests/test_worker.py \
  tests/test_diagnosis.py tests/test_error_signature.py
```

영문 원문: [Core lifecycle contracts](core-lifecycle-contracts.md)
