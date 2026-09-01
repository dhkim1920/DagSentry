# 복구 검사기

Recovery Checker는 활성 Incident를 현재 Airflow TaskInstance 상태와 비교하는 일회성 프로세스입니다.
Kubernetes CronJob 같은 외부 스케줄러에서 매분 실행합니다.

```shell
uv run dagsentry-recovery-checker
```

이 프로세스는 API와 Diagnosis Worker와 같은 DB, Airflow API, Notification 설정을 사용합니다. 활성화 전
마이그레이션 `0009`까지 적용해야 하며 Airflow token에는 다음 읽기 권한이 필요합니다.

```text
GET /api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances
```

요청에는 정확한 `task_id`, `map_index` 필터를 넣으며 Airflow Metadata DB를 직접 읽지 않습니다.

## 복구 정책

각 `OPEN`/`ACKNOWLEDGED` Incident에 연결된 Failure Event의 TaskInstance 식별자를 중복 제거해 현재
상태를 조회합니다. 모든 응답이 `SUCCESS`일 때만 Incident를 `RECOVERED`로 바꿉니다. `RUNNING`,
`FAILED`, queued, deferred, null 상태는 바꾸지 않습니다. Airflow 타임아웃·전송 오류·HTTP 오류·작업
누락·잘못된 응답도 상태를 바꾸지 않으며 다음 스케줄에서 다시 확인합니다. `RESOLVED`, `IGNORED`는
자동 선택·변경하지 않습니다.

전이와 대기 복구 알림은 하나의 DB 트랜잭션으로 저장합니다. Webhook은 안정적인 `Idempotency-Key`를
받으며 전송이 실패해도 Incident는 `RECOVERED`로 남고 다음 실행이 새 전이 없이 저장 알림을 재시도합니다.
명령은 JSON 요약을 출력하고 Airflow 조회 또는 알림 전송에 실패하면 스케줄러가 저하 실행을 기록하도록
non-zero로 종료합니다.

영문 원문: [Recovery Checker](recovery-checker.md)
