# 설치 및 로컬 검증

DagSentry 설치는 PostgreSQL database와 migration, Ingest API token, Airflow Listener/Retry Callback collector, API와
Worker process, Notification Provider 설정으로 구성됩니다. deployment environment, DB URL, Ingest token, Airflow API
endpoint/token, 선택 LLM, 선택 notification credential을 Secret mechanism으로 설정하고 `uv run alembic upgrade head`를
먼저 적용합니다.

API와 Worker를 시작하고 `/health/live`, `/health/ready`를 확인합니다. Airflow에는 `dagsentry[airflow]` package를
설치해 provider entry point를 발견시키고 모든 Airflow component에 Ingest URL/token을 구성합니다. retry callback은
기존 task policy를 대체하지 않고 `install_retry_callback` helper로 추가합니다. 실제 실패 또는 bounded test로
Failure Event, Outbox, Diagnosis, Notification delivery를 확인합니다.

운영 구성, Provider별 설정, Recovery Checker/Reconciler/일일 report 배포는 최신 개별 문서를 함께 참조하세요.

영문 원문: [Installation](installation.md)
