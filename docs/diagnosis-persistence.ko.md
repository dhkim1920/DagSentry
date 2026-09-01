# Diagnosis 저장과 재사용

DagSentry는 완료한 Rule/AI diagnosis를 `diagnoses`에 저장합니다. 한 행에는 source, validation 상태,
분류, 신뢰도와 근거, 정확히 마스킹된 Evidence 줄, 권장 조치, 재시도 판단, 이를 만든 schema/prompt/rule
버전이 기록됩니다.

`REUSED` 행은 Diagnosis 내용을 복사하지 않습니다. 새 Failure Event와 Error Signature, 호환성 버전만
유지하고 `reused_from_diagnosis_id`로 원본 Rule/AI 행을 직접 가리킵니다. 재사용 조회는 `REUSED` 행을
제외하므로 반복 실패가 참조 체인을 만들지 않습니다.

## 재사용 계약

LLM 호출을 고려하기 전에 호출자가 `DiagnosisReusePolicy`를 제공합니다. 저장 Diagnosis는 Error Signature
ID와 요청 `fingerprint_version`이 일치하고, `PASSED`이며, 다른 `REUSED`가 아닌 원본 `RULE` 또는 `AI`
행이어야 합니다. `NULL`을 포함해 diagnosis schema/prompt/rule 버전이 정책과 정확히 같고 `created_at`이
명시한 `max_age` 안에 있어야 합니다. `UNSIGNABLE`, `REJECTED`, 오래되었거나 버전 비호환 행은 무시하며
호환 원본이 여러 개면 가장 최신을 선택합니다.

LLM Provider를 호출하기 전 `reuse_diagnosis`를 호출합니다. 결과가 있으면 현재 실패는 참조로 해결되어
Provider 호출이 필요 없고, 없으면 AI Diagnosis로 진행합니다.

## 운영자 조회 API

Diagnosis History는 Incident API와 같은 읽기 전용 `X-DagSentry-Viewer-Token` 또는
`X-DagSentry-Operator-Token`을 받습니다.

```text
GET /api/v1/diagnoses
GET /api/v1/diagnoses/{diagnosis_id}
```

목록은 최대 `limit` 100의 offset pagination이며 `source`, `validation_status`, 해석된
`classification`, `error_signature_id`, `failure_event_id`, inclusive UTC `date_from`/`date_to`로
필터할 수 있습니다. `created_at` 또는 해석된 `confidence`로 안정 정렬합니다. 상세 응답은 정확히
마스킹된 Evidence, 추출값, 권장 조치, Failure Try, Incident ID, Error Signature, 유효 전송의 Airflow
로그 URL을 포함하지만 원본 로그, Provider 응답, credential은 노출하지 않습니다.

영문 원문: [Diagnosis persistence and reuse](diagnosis-persistence.md)
