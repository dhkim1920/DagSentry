# 운영

지원하는 single-host production 배포와 Docker Secret 절차는 [production deployment](production-deployment.md)를
참조합니다.

## 프로세스 경계

PostgreSQL, `dagsentry-api`, `dagsentry-worker`, `dagsentry-scheduler`를 분리 서비스로 실행합니다.
`dagsentry-recovery-checker`는 주기적인 one-shot process로 스케줄하고, Daily Report는 DB 설정을 읽는
scheduler가 실행합니다. Airflow에는 Listener, retry callback, Reconciler에만 동일 DagSentry distribution을
설치합니다. Worker와 Recovery Checker는 `.env.example`의 Airflow
API와 notification 설정이 필요하며 Worker와 report는 선택적으로 LLM도 사용합니다.

```shell
uv run alembic upgrade head
uv run dagsentry-api
uv run dagsentry-worker
uv run dagsentry-recovery-checker
uv run dagsentry-scheduler
```

두 프로세스는 정상 종료를 처리합니다. API는 Uvicorn에 graceful connection drain을 위임하고 Worker는
SIGINT/SIGTERM에서 새 job claim을 멈춘 뒤 소유 job을 마무리·기록하고 종료합니다. 재시작 Worker는 pending
job을 계속하며 이미 저장된 Diagnosis나 전송 완료 Notification을 반복하지 않습니다.

## 실패 검사

job 지연/실패 시 `daily_report_schedules`, `scheduler_heartbeats`, `daily_report_schedule_runs`, `daily_reports`,
`failure_events`, `diagnosis_outbox`, `error_signatures`, `diagnoses`, `incidents`,
`incident_state_transitions`, `notification_deliveries`, `incident_recovery_notifications`,
`operational_metric_counters`, `daily_reports` 순서로 검사합니다. Worker `last_error`는 stage, category,
exception type, 잘린 message가 있는 제한 JSON입니다. raw Task log, credential, Provider response body, API key는
로그나 저장 오류 값에 넣지 않습니다. `PENDING`은 `available_at`까지 대기하고 오래된 `PROCESSING` lock은
retry가 남아 있으면 자동 회수합니다. `DEAD`는 영구 오류 또는 시도 소진이며 운영자 검사가 필요합니다.

```shell
dagsentry-worker-jobs list-dead
dagsentry-worker-jobs requeue
```

Prometheus scrape, metric 의미, dashboard, alert threshold는 [metrics](metrics.md)를 참조합니다.

영문 원문: [Operations](operations.md)
