# SMTP 이메일 알림 Provider

DagSentry는 SMTP 서버를 통해 장애 진단, Incident 복구, 일일 리포트 알림을 일반 텍스트 이메일로
보낼 수 있습니다.

## 설정

환경변수 기반 Notification 설정으로 SMTP를 구성합니다.

```text
DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=environment
DAGSENTRY_NOTIFICATION_PROVIDER=smtp
DAGSENTRY_SMTP_HOST=smtp.example.com
DAGSENTRY_SMTP_PORT=587
DAGSENTRY_SMTP_USERNAME=alerts@example.com
DAGSENTRY_SMTP_PASSWORD=replace-with-an-app-password
DAGSENTRY_SMTP_FROM=alerts@example.com
DAGSENTRY_SMTP_TO=["oncall@example.com","platform@example.com"]
DAGSENTRY_SMTP_USE_STARTTLS=true
```

`DAGSENTRY_SMTP_TO`는 JSON 배열입니다. 인증은 선택 사항이지만 사용자 이름과 비밀번호는 반드시
함께 설정해야 합니다. STARTTLS는 기본으로 활성화됩니다. 일반적으로 465 포트에서 쓰는 implicit TLS는
`DAGSENTRY_SMTP_USE_SSL=true` 및 `DAGSENTRY_SMTP_USE_STARTTLS=false`로 설정합니다.

재시도 범위는 `DAGSENTRY_SMTP_TIMEOUT_SECONDS`, `DAGSENTRY_SMTP_MAX_ATTEMPTS`,
`DAGSENTRY_SMTP_RETRY_BACKOFF_SECONDS`로 조정할 수 있습니다.

## 전송 동작

이메일 제목과 본문은 장애, 복구, 일일 리포트에 맞춰 결정적으로 생성됩니다. 추적을 위해 안정적인
delivery key가 `X-DagSentry-Delivery-Key` 헤더와 본문에 포함됩니다. SMTP에는 이식 가능한 멱등성
기능이 없으므로 서버가 수신한 직후 네트워크 오류가 발생하면 재시도로 중복 이메일이 발송될 수 있습니다.
다만 DagSentry의 영속 전송 상태는 일반적인 반복 전송을 방지합니다.

SMTP는 현재 Managed Connection(DB 설정)이 아닌 환경변수 설정만 지원합니다.

영문 원문은 [SMTP Email Notification Provider](smtp-provider.md)에서 확인할 수 있습니다.
