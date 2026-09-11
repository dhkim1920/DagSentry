# Notification Provider 계약

Teams primary와 SMTP fallback은 환경 설정에서 `DAGSENTRY_NOTIFICATION_PROVIDER=teams`,
`DAGSENTRY_NOTIFICATION_FALLBACK_PROVIDERS=["smtp"]` 및 두 Provider의 연결 값을 지정합니다.
기존 설치의 기본 fallback 목록은 빈 배열입니다. 순서대로 시도해 첫 성공에서 종료하고
`teams>smtp` 전달 기록 한 행과 동일 전달 키를 사용합니다. 모두 실패하면 primary 오류로 재시도를
판단합니다. 로그에는 Provider 이름과 오류 분류만 남깁니다. Daily Report와 Recovery의 재시도는
기존 Provider가 현재 체인의 구성원이면 재개합니다. DB 연결의 알림 범위는 기존 Slack만 유지합니다.

DagSentry의 재사용 가능한 계약 테스트는 Diagnosis 또는 Incident Core에 벤더 분기를 추가하지 않고 HTTP
Notification Provider가 공유하는 동작을 검증합니다. Webhook, Slack, Teams, Discord 어댑터가 이 계약을
구현합니다.

## 필수 동작

각 구현은 Provider 중립 `NotificationPayload`와 안정적인 `delivery_key`를 받고, 비어 있지 않은 안정적
`name`과 성공 HTTP 상태를 반환해야 합니다. 벤더 표현 안에 모든 필수 중립 payload 필드와 수신자가 볼 수
있는 추적 또는 멱등성 필드의 delivery key를 유지합니다. `408`, `429`, `5xx`, 타임아웃, 네트워크 오류는
제한된 횟수로 재시도하고 실패를 `NotificationErrorCategory` 및 올바른 `retryable` 판단으로 매핑합니다.
응답 본문, transport 예외 세부 정보, credential은 오류에 복사하지 않습니다.

벤더별 레이아웃, card/block 구조, endpoint 경로, 인증 헤더는 의도적으로 공통 계약 밖에 둡니다. 각
어댑터는 구체 `assert_success_request`와 Provider별 테스트로 이를 검증합니다.

## 구현 추가

`tests.contracts.notification.NotificationProviderContract`를 상속한 테스트 클래스를 만들고 다음 hook을
구현합니다.

- `make_provider`: 두 번의 시도, 전달받은 mock HTTP handler와 sleep 함수로 어댑터를 구성합니다.
- `assert_success_request`: 벤더 요청을 해석해 중립 payload와 delivery key가 보존되었음을 증명합니다.

Pytest는 전체 계약을 자동 상속합니다.

```shell
uv run pytest tests/test_webhook_provider.py
```

Provider별 설정 검증과 credential 마스킹 테스트는 구체 어댑터 테스트 옆에 둡니다.

영문 원문: [Notification Provider contract](notification-provider-contract.md)
