# 알림 전송

Diagnosis 파이프라인은 Webhook, Slack, Teams, Discord, SMTP 어댑터를 통해 Provider 중립 payload를
전송합니다. Core 코드는 `NotificationProvider`에만 의존하며 Provider별 HTTP 세부 사항은 어댑터에 둡니다.

payload에는 Failure, Diagnosis, Incident ID와 Incident 상태·실패 수, 환경, DAG/run/task/Try 식별자,
실패 시각, 분류, 근본 원인, 신뢰도, 마스킹된 Evidence, Error Signature, 권장 조치, 재시도 판단,
Airflow 로그 링크, Diagnosis 소스, 명시적 `is_rule_fallback` 플래그가 들어갑니다. 로그나 LLM을 쓸 수
없을 때도 Rule fallback payload는 유용합니다.

## 전송 보장

`notification_deliveries`는 유효 Diagnosis마다 버전이 있는 SHA-256 delivery key, Provider 이름과 전송
상태, 시도 수와 정제된 실패 분류, 마지막 HTTP 상태 및 전송 시각, 정확한 JSON payload snapshot 한 행을
저장합니다.

최초 실패를 발송하고 활성 Incident당 최종 FAILED 이벤트를 추가 1회 발송합니다. 최초부터 FAILED면
두 대상이 같은 이벤트이므로 한 번만 발송합니다. 0019는 기존 Incident의 최초 FAILED도 선점 처리합니다.
조건부 UPDATE로 최종 이벤트를 선점하며 재시도는 기존 전달 키와 완료 진단을 사용합니다.
반복 retry는 REPEATED_ACTIVE_INCIDENT, 반복 FAILED는 REPEATED_FINAL_FAILURE로 억제합니다.

Teams와 SMTP는 한국어 라벨과 기본 Asia/Seoul 시각을 사용하며 `DAGSENTRY_DISPLAY_TIMEZONE`으로
변경할 수 있습니다. Slack·Discord도 실패 상태를 구분합니다. 저장 payload에는 `failure_state`가
있지만 Webhook은 추가 필드를 거부하는 엄격한 수신자와 기존 기계 계약을 유지하도록 이 필드를 제외합니다.

시간 또는 횟수 기반 reminder 정책은 없습니다. cadence와 reset 동작의 운영 요구사항이 정해진 뒤에만
reminder를 추가합니다. Incident가 더 이상 활성 상태가 아니면 일치하는 이후 Failure는 새 Incident와
새 전체 알림을 만듭니다.

Webhook 요청은 안정 키를 `Idempotency-Key`로 보냅니다. DagSentry는 전송 행을 잠가 동시 Worker가
Provider를 두 번 호출하지 않게 합니다. 전송 완료 Diagnosis를 다시 처리하면 DB만 처리하는 no-op입니다.
원격 HTTP 응답과 로컬 DB 트랜잭션을 원자적으로 확정할 수 없으므로 수신자도 키를 강제해야 합니다.

Webhook 실패는 저장된 Failure Event나 Diagnosis를 rollback하지 않습니다. 타임아웃, HTTP 408/429,
5xx, 전송 오류는 제한된 범위에서 재시도한 뒤 Outbox Worker에 반환됩니다. 인증·권한 및 다른 4xx 오류는
영구 실패입니다.

영문 원문: [Notification delivery](notification.md)
