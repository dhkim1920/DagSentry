# 운영 메트릭

DagSentry는 Ingest API의 Prometheus text metric을 노출합니다.

```text
GET /metrics
```

서비스 시작 전 migration `0010`까지 적용합니다. counter는 PostgreSQL에 저장되므로 별도 API, Worker,
Recovery Checker process가 만든 결과를 API가 노출할 수 있고 process restart로 초기화되지 않습니다. backlog,
processing delay, 현재 Incident count는 Prometheus scrape 시점의 DB 상태에서 계산합니다. endpoint는
Prometheus 호환을 위해 의도적으로 인증하지 않으므로 ingress, service mesh, firewall에서 monitoring network로
제한해야 합니다.

## 메트릭

주요 metric은 `dagsentry_ingest_events_total`(Ingest 결과), `dagsentry_outbox_jobs`(상태별 Outbox),
`dagsentry_outbox_oldest_ready_age_seconds`, `dagsentry_worker_jobs_total`,
`dagsentry_log_collection_total`, `dagsentry_diagnosis_outcomes_total`,
`dagsentry_notification_attempts_total`, `dagsentry_incident_events_total`, `dagsentry_incidents`,
`dagsentry_recovery_checker_runs_total`, `dagsentry_recovery_checker_delay_seconds`입니다. 모든 label value는
애플리케이션 allowlist로 제한하며 DAG ID, Task ID, environment, UUID, 오류/exception 텍스트, raw Task log는
metric label이 아닙니다.

## Dashboard와 초기 alert

최소 dashboard에는 5분 Ingest 생성/중복/실패율, 상태별 Outbox와 oldest-ready age, Worker retry/dead/stale
reclaim 증가, log-unavailable 비율과 Diagnosis 결과, notification failure 증가, Incident 상태·opened/recovered
증가, Recovery Checker delay·degraded run을 포함합니다. 제안 PromQL은 영문 원문의 code block을 사용합니다.

정상 production traffic 관측 후 threshold를 조정합니다. 초기값은 10분간 `PENDING` backlog 100 초과, 10분간
oldest-ready 300초 초과(5분간 900초 초과는 critical), 5분간 dead job/failed notification 증가, 15분간 20회 이상
수집에서 10% 초과 log-unavailable 비율, 15분간 `provider_error`/`evidence_rejected` 증가, Recovery Checker
delay 300초 초과 또는 degraded run입니다. 새 Prometheus target 직후 counter rate/increase는 잠시 unavailable일
수 있으나 DB gauge alert는 계속 쓸 수 있습니다.

영문 원문: [Operational Metrics](metrics.md)
