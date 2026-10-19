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

## 운영 현황

Admin은 **설정 → 운영 현황** (`/ui/?view=admin&section=operations`)에서 API·DB·Worker·Scheduler 상태와
진단 큐를 확인합니다. 세션 전용 `GET /api/v1/admin/operations`도 같은 전체 환경 집계를 반환합니다.
자동 갱신은 없으며 **새로고침**으로 관측 시점을 갱신합니다.

업데이트한 API·Worker를 시작하기 전에 `alembic upgrade head`로 revision `0022`를 적용합니다.
Worker도 새 버전으로 재시작해야 `worker_heartbeats`에 프로세스별 heartbeat를 기록합니다.
기본 기록 간격은 10초이며 작업 처리 중에도 기록합니다. API는 종료 기록이 없고 마지막 기록 이후
30초 이내인 인스턴스를 실행 중으로 판단합니다. 설정은 `DAGSENTRY_WORKER_HEARTBEAT_INTERVAL_SECONDS`와
`DAGSENTRY_WORKER_OFFLINE_AFTER_SECONDS`이며, API의 판정 시간을 Worker의 기록 간격보다 길게 유지합니다.
정상 종료는 즉시 중단 상태로 기록하고 강제 종료는 판정 시간 이후 중단으로 표시합니다.
Scheduler는 기존 heartbeat와 `DAGSENTRY_SCHEDULER_OFFLINE_AFTER_SECONDS`를 사용합니다.

기록이 없으면 **관측 정보 없음**으로 표시합니다. 이전 버전 Worker의 최근 작업 성공만으로 실행 중이라
판단하지 않습니다. heartbeat는 프로세스 존재를 나타내며 작업 진행이나 외부 연결 성공을 보장하지 않습니다.
최근 성공 시각은 보존된 완료 진단 작업과 성공 리포트 실행에서 집계하며 수동 리포트 실행도 포함합니다.

큐는 전체 대기, 즉시 실행 가능, 재시도 대기, 처리 중, 오래된 처리 잠금, 실패, 완료를 구분합니다.
오래된 잠금은 API의 `DAGSENTRY_WORKER_STALE_LOCK_TIMEOUT_SECONDS`를 기준으로 하므로 Worker 설정과
맞춥니다. 최장 실행 대기는 실행 가능한 가장 오래된 `available_at` 이후 시간이며 지연 재시도는 제외합니다.
조회 실패 시 이전 작업 수를 지우고 확인 불가로 표시합니다. `/health/ready`로 DB 연결을 별도 확인하지만
연결 성공이 마이그레이션 정상이나 큐 조회 성공을 뜻하지는 않습니다. 원시 오류·자격 증명·호스트명은 노출하지 않습니다.

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

로컬에서는 `uv run dagsentry-api --with-scheduler`로 API와 스케줄러를 함께 시작합니다.
별도 자식 프로세스가 같은 환경 변수·`.env`를 읽으며 로그를 현재 터미널에 출력합니다.
Ctrl+C/SIGTERM에서 둘 다 정상 종료를 요청하고 최대 5분 후 남은 프로세스를 강제 종료합니다.
한쪽의 조기 종료는 나머지 종료와 명령의 실패 상태로 이어집니다. DB·Worker·migration은 기존처럼
별도로 준비합니다. 운영 Compose는 기존 API·scheduler 서비스 분리 구성을 사용합니다.

영문 원문: [Operations](operations.md)

만료 세션·오래된 Worker heartbeat 정리와 정기 백업 timer는 [maintenance](maintenance.md)를 참조합니다.
정리 명령은 기본 미리보기이며 `--apply`를 지정해야 삭제합니다. 진단·장애·리포트·감사 이력은 유지합니다.
