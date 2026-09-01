# Error Signature

DagSentry는 결정적인 Error Signature로 동등한 failure를 묶습니다. AI는 이 identity를 만들거나 바꾸지 않습니다.

## Fingerprint v1

v1 fingerprint는 `operator_type`, `exception_class`, `vendor_error_code`, `normalized_message`,
`application_stack_frame` 다섯 필드만 사용합니다. 값은 이미 마스킹된 Relevant Log Excerpt에서만 고르고,
whitespace를 collapse하며 vendor code는 uppercase, 누락값은 명시적 JSON `null`로 둡니다. 다섯 key를
lexicographically sorted compact UTF-8 JSON으로 직렬화한 `SHA256`이 fingerprint입니다. 필드 선택·정규화·JSON
직렬화·hashing 변경은 v1을 고치지 않고 새 version을 만들어야 합니다.

최신 exception class/vendor code/application stack frame과, 해당 예외·vendor code 또는 명시적 error/failure/OOM/
HTTP error를 가진 최신 normalized message를 사용합니다. operator name 또는 stack frame 하나만으로는 묶지 않으며
exception class, vendor code, stable error message가 모두 없으면 `UNSIGNABLE`로 DB row를 만들지 않습니다.
DB는 `(fingerprint_version, fingerprint)` unique를 강제하고 concurrent worker insert는 같은 Signature ID를 받습니다.

## 운영자 API

```text
GET /api/v1/error-signatures
GET /api/v1/error-signatures/{signature_id}
GET /api/v1/error-signatures/{signature_id}/occurrences
GET /api/v1/error-signatures/{signature_id}/trend
```

목록은 environment/DAG/task/classification/query/date filter와 allowlisted sort를 지원합니다. detail은 canonical
fingerprint, occurrence 통계, 최신 검증 non-`REUSED` Diagnosis 요약을 반환하되 raw Task log, Provider response,
credential은 노출하지 않습니다.

영문 원문: [Error Signature](error-signature.md)
