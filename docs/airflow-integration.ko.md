# Airflow 3 Failure 수집

지원 선언 범위는 Airflow `>=3.1.8,<4`이며 `apache_airflow_provider`와 `airflow.plugins`
엔트리포인트를 모두 등록합니다. 3.1.8과 3.3.1에서 Provider 발견과 기본 lazy 설정의 Listener
등록을 각각 검사합니다. 패키지 호환 검사는 실행 중인 Airflow 서버의 실패 수집 실연동을 대신하지 않습니다.

DagSentry는 Listener가 재시도 가능 실패와 최종 실패를 모두 관찰하므로 Airflow public extension point 두 개를
사용합니다.

- `on_task_instance_failed`는 결과 상태가 `FAILED`인 TaskInstance만 보냅니다.
- `on_retry_callback`은 결과 상태가 `UP_FOR_RETRY`인 현재 실패 Try를 보냅니다.

두 경로는 같은 Failure Ingest payload를 만들며 Airflow가 어느 경로를 여러 번 호출해도 server-side
`event_key`가 최종 멱등성 경계입니다.

## 설치

API server, DAG processor, scheduler, task worker를 실행하는 모든 Airflow image에 Airflow dependency와 함께
DagSentry를 설치합니다.

```shell
pip install 'dagsentry[airflow]'
AIRFLOW__CORE__LAZY_DISCOVER_PROVIDERS=False airflow plugins
```

DagSentry는 `apache_airflow_provider` entry point를 게시하므로 installed wheel에서 Listener plugin을 자동
발견·등록하며 `$AIRFLOW_HOME/plugins`에 파일을 복사할 필요가 없습니다. 모든 component에 collector 설정을
지정합니다.

```shell
export DAGSENTRY_ENVIRONMENT=production
export DAGSENTRY_INGEST_URL=http://dagsentry:8000/api/v1/failure-events
export DAGSENTRY_INGEST_API_TOKEN=replace-with-the-server-token
```

## 재시도 callback 정책과 전송

Airflow는 `airflow_local_settings.py`에서 cluster policy를 읽습니다. 기존 `task_policy`를 대체하지 말고
DagSentry helper를 추가합니다.

```python
from dagsentry.airflow.policy import install_retry_callback


def task_policy(task):
    install_retry_callback(task)
```

기존 retry callback이 있으면 순서를 유지한 채 DagSentry callback을 뒤에 붙이며 정책을 여러 번 적용해도
중복을 만들지 않습니다. 수집은 DAG/run/task ID, map index, Try number, end time, operator type의 public
TaskInstance 필드만 사용합니다. HTTP 요청은 2초 timeout과 최대 2번 시도를 사용하고 connection/timeout,
HTTP 429/5xx만 재시도합니다. collector 오류는 task, Listener, policy 실행으로 전파하지 않습니다. UI/CLI
callback 상태 변경은 task callback을 보장하지 않으므로 Reconciler가 Airflow Public REST API로 누락 update를
복구합니다.

영문 원문: [Airflow 3 Failure Collection](airflow-integration.md)
