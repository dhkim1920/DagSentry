# Airflow Task Log 수집

DagSentry는 Airflow 3 Public REST API로 log를 읽으며 Airflow Metadata DB를 조회하지 않습니다. 환경 기반
설정 또는 Failure Event 환경과 일치하는 활성 Airflow Managed Connection 중 하나만 선택합니다.

```shell
DAGSENTRY_AIRFLOW_CONFIG_SOURCE=environment
DAGSENTRY_AIRFLOW_API_BASE_URL=http://airflow-api-server:8080
DAGSENTRY_AIRFLOW_API_TOKEN=replace-with-an-airflow-bearer-token
```

token에는 DAG task log 읽기 권한이 필요하며 HTTP Bearer token으로만 보내고 설정 표시에서 redaction합니다.
timeout, max attempts, retry backoff, max response bytes는 환경변수로 조정할 수 있습니다. database mode는 job 시작마다
API/UI URL, token, timeout, retry, response size의 immutable snapshot을 읽으며 누락/비활성 연결, token/config
오류, 복호화 실패 시 환경값 fallback 없이 fail closed 및 retry합니다.

Airflow 3.2.2에서 한 Task Try log는 다음 endpoint입니다.

```text
GET /api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}/logs/{try_number}
    ?map_index={map_index}&full_content=false
```

DagSentry는 JSON을 요청하고 순서대로 continuation token을 따르며 반복 token을 거부합니다. 모든 page에 걸쳐
response byte limit을 적용합니다. transport failure, timeout, HTTP 429/5xx만 재시도하며 auth, missing Try,
remote log unavailable, oversized/malformed response는 unhandled exception이 아닌 typed `LOG_UNAVAILABLE`입니다.

영문 원문: [Airflow Task Log Collection](task-log-collection.md)
