# Worker 복구

재시도 예약은 WARNING, DEAD 전환은 ERROR로 기록합니다. 로그에는 안전한 식별자와 예외 타입만
남기며 예외 원문이나 DB traceback을 출력하지 않습니다. Outbox 오류 정보도 단계·분류·타입과
고정 안내 문구를 저장하고 예외 원문을 복사하지 않습니다.

DagSentry는 Worker가 완료 처리 없이 종료해 `PROCESSING`에 남은 Diagnosis Outbox 작업을 자동 복구합니다.
기본 stale-lock timeout은 900초이며 `DAGSENTRY_WORKER_STALE_LOCK_TIMEOUT_SECONDS`로 변경합니다.

timeout은 Diagnosis pipeline 한 번의 최대 예상 시간보다 길게 정합니다. lock이 만료되면 다른 Worker가
원자적으로 소유권을 얻고 `attempt_count`를 증가시킵니다. 완료/실패 갱신은 lock 소유자와 시도 번호를
검증하므로 원래 Worker는 더 이상 해당 시도를 완료할 수 없습니다.

이미 `DAGSENTRY_WORKER_MAX_ATTEMPTS`에 도달한 stale job은 다시 claim하지 않고 `DEAD`가 됩니다.
재시도 가능한 poison job도 최대 시도 횟수로 제한하므로 `DEAD`가 된 뒤 다음 job이 진행됩니다.

## DEAD job 검사와 requeue

실패 job을 최대 100개 newline-delimited JSON으로 조회합니다.

```shell
uv run dagsentry-worker-jobs list-dead --limit 100
```

원인을 확인·수정한 뒤 ID 하나로 정확히 한 job을 재대기열에 넣습니다.

```shell
uv run dagsentry-worker-jobs requeue --outbox-id <outbox-uuid>
uv run dagsentry-worker-jobs requeue --failure-event-id <failure-event-uuid>
```

`DEAD` job만 requeue할 수 있습니다. requeue는 `attempt_count`를 0으로 재설정하고 이전 오류와 lock 필드를
지운 뒤 새 제한 재시도 주기에 즉시 사용할 수 있게 합니다. Failure Event나 이전 Diagnosis/Notification
기록은 삭제하지 않습니다.

영문 원문: [Worker Recovery](worker-recovery.md)
