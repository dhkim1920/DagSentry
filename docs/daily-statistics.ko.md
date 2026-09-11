# 일일 통계 v2

DagSentry는 `aggregate_daily_statistics`로 일일 리포트 입력을 만듭니다. 모든 값은 SQLAlchemy SQL 표현식으로
PostgreSQL에서 계산하며 LLM이나 리포트 문장이 숫자를 바꿀 수 없습니다.

## 기간과 범위

리포트는 다음 로컬 달력 하루를 UTC로 변환해 조회합니다(SQLite도 UTC로 비교).

```text
[로컬 report_date 자정, 다음 로컬 자정), UTC로 변환
```

리포트는 정규화한 환경 하나에 한정됩니다. 첫 자정의 값은 포함하고 다음 자정의 값은 다음 리포트에 속합니다.

## 지표 정의

- `failure_attempts`: 기간의 `observed_at`을 가진 Failure Event입니다. 실패한 retry attempt도 각각 셉니다.
- `affected_task_instances`: 해당 Failure Event의 고유 `dag_id`, `dag_run_id`, `task_id`, `map_index`입니다.
  하나의 mapped TaskInstance의 다른 Try는 한 번만 셉니다.
- `affected_dag_runs`: 고유 `dag_id`, `dag_run_id`입니다.
- `incidents.new`: 기간에 만든 Incident입니다.
- `incidents.unresolved`: 기간 끝 이전에 만들고 그 시점까지 `RECOVERED`/`RESOLVED`/`IGNORED`가 아닌
  Incident입니다. 이후 운영자 변경은 과거 값을 고치지 않습니다.
- `incidents.recovered`: 기간에 `RECOVERED`로 전이한 고유 Incident입니다.
- `error_signatures.new`/`repeated`: 같은 환경에서 이전 검증 Diagnosis가 없는/있는 기간 내 Signature입니다.
- `classification_counts`: 모든 `ErrorClassification`별 Failure 수입니다. `REUSED`는 원본 Diagnosis 분류를
  사용하며 검증 Diagnosis가 없는 Failure에는 추측 분류를 부여하지 않습니다.
- `mean_time.recovery_seconds`와 `resolution_seconds`: 기간의 해당 Incident 전이에 대한 생성부터 전이까지의
  평균 시간입니다.

빈 기간은 모든 분류의 0 count와 `null` 평균 시간을 반환합니다.

## 버전 JSON 계약

`DailyStatistics`는 알 수 없는 필드를 금지한 frozen Pydantic 계약입니다.
`DailyStatistics.model_json_schema()`는 `schema_version=2`, IANA `timezone`을 사용합니다. consumer는
지원하지 않는 schema version을 필드 의미를 임의 재해석하지 말고 거부해야 합니다.

top_failures는 DAG/Task별 실패 이벤트 수 내림차순 상위 20개입니다. 분류·Incident·원인 설명은
가장 최근 진단된 실패에서, 마지막 실패 시각은 진단 유무와 관계없이 집계합니다.

영문 원문: [Daily Statistics v2](daily-statistics.md)
