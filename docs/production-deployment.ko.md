# 운영 배포

DagSentry가 지원하는 production topology는 PostgreSQL, API, Worker, one-shot Recovery Checker/일일 리포트 job을
분리한 single-host Linux Docker Compose 배포입니다. image와 package, migration은 동일 immutable build를 사용하고
credential은 Docker Secret 또는 배포 Secret mechanism으로 runtime에 주입합니다. `.env`에는 placeholder만 두고
real Secret을 commit하거나 image에 포함하지 않습니다.

배포 전 PostgreSQL backup과 connection encryption key/version 보관을 확인하고 migration을 적용합니다. API readiness,
Worker 처리, Airflow collector Ingest, notification/LLM Provider connectivity, Prometheus network restriction을 검증합니다.
public traffic에는 TLS를 적용하고 `/metrics`, health endpoint는 service network/ingress에서 제한합니다. rolling/multi-host
orchestration과 non-container production 설치는 현재 compatibility target이 아닙니다.

upgrade는 writer quiesce, forward migration, 같은 build의 모든 process 교체, readiness와 bounded functional check, traffic
재개 순서입니다. rollback은 Alembic downgrade가 아니라 이전 DB backup과 matching application image/encryption key를
복원합니다.

영문 원문: [Production deployment](production-deployment.md)
