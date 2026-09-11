# Failure Reconciler

Reconciler는 Airflow Listener, 재시도 callback, DagSentry Ingest API가 일시적으로 사용할 수 없을 때
놓친 Failure Event를 복구합니다. Airflow 3 Public REST API만 사용하고, 실시간 collector와 같은 인증된
Failure Ingest endpoint로 복구 실패를 보냅니다. Airflow Metadata DB는 읽지 않습니다.

## 스캔과 watermark 계약

각 실행은 UTC task 시작 시각을 상한으로 고정합니다. scan window 안에서 `updated_at`이 바뀐 현재
TaskInstance를 페이지 단위로 나열하고, 정확한 `dag_id`, `dag_run_id`, `task_id`, `map_index`의
완전한 Try 이력을 가져옵니다. 과거 `failed`, `up_for_retry` 상태는 `RECONCILER` Failure Event가 되고
다른 상태는 무시합니다.

첫 요청은 `limit`, `offset=0`, `order_by=id`를 사용합니다. 응답에 `next_cursor`가 있으면
커서 방식으로 전환하고 명시적 null이면 종료합니다. 필드가 없으면 실제 받은 행 수만큼 offset을
늘립니다. 짧은 페이지로 종료하지 않으며 빈 페이지 또는 유효한 0 이상의 정수 `total_entries`로
종료를 판단합니다. 최대 10,000페이지를 초과하면 watermark를 전진시키지 않고 실패합니다.

완전히 처리한 마지막 상한은 Airflow Variable에 ISO 8601으로 저장합니다. 기본 키는
`dagsentry_reconciler_watermark_{environment}`이며 완료 watermark를 5분 overlap으로 다시 읽고 첫 실행은
기본 24시간을 되돌아봅니다. 모든 페이지·Try 이력·Ingest 요청이 성공한 뒤에만 watermark가 전진합니다.
부분 전송 뒤 실패하면 task 재시도나 다음 실행이 같은 window를 다시 실행하며 versioned `event_key`가
at-least-once replay를 멱등적으로 만듭니다.

## 배포와 제한

Airflow extra로 DagSentry를 설치한 뒤 `dags/dagsentry_reconciler.py`를 DAG bundle에 복사 또는 mount합니다.
DAG는 5분마다 실행되고 catchup을 끄며 활성 실행 하나, reconciliation task 재시도 두 번을 허용합니다.
기존 collector/Airflow API 설정과 `.env.example`의 선택 tuning 값을 구성합니다. API bearer token에는
TaskInstance와 Try 조회 권한, DAG task에는 watermark Variable 읽기/쓰기 권한, Ingest token에는 기존
Failure Event POST 권한만 필요합니다. Airflow API와 Ingest API에는 별도 secret을 사용합니다.

API 호출은 기본 10초 timeout과 최대 3번 시도이며 기본 요청 크기는 100 TaskInstance입니다.
서버가 더 적은 행을 반환할 수 있습니다. HTTP 429/5xx,
네트워크 오류, timeout만 제한적으로 재시도하고 인증·영구 HTTP 오류는 watermark를 전진시키지 않고 DAG를
실패시킵니다. 오래된 Airflow TaskInstance 이력을 보관하지 않았다면 해당 실패는 복구할 수 없습니다.

영문 원문: [Failure Reconciler](reconciler.md)
