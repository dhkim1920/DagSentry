# 보안 점검 목록

배포 전에는 PostgreSQL role을 최소 권한으로 제한하고 DB/Secret manager/backup 접근을 분리하며, Ingest·Viewer·Operator
token과 Airflow/Provider credential을 서로 독립적으로 발급합니다. 모든 Secret은 runtime에 주입하고 Git, image,
command line, shell history, log, metric, ticket에 남기지 않습니다. connection encryption key는 32-byte Base64 value와
version을 안전하게 보관합니다.

배포 후에는 API authentication·cross-token rejection·CSRF·role authorization, Admin audit, Managed Connection secret
non-disclosure, Airflow/LLM/notification 최소 scope, TLS, private Ollama network, health/metrics network restriction을
검사합니다. PostgreSQL backup은 encrypted access-controlled location에 두고 isolated restore를 정기 연습하며 matching
encryption key/version 복구를 검증합니다. 운영 중에는 credential rotation, dependency/security update, alert review,
access review, backup restore test, audit log review를 정기 수행합니다.

영문 원문: [Security Checklist](security-checklist.md)
