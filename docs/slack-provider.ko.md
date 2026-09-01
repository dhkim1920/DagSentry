# Slack Notification Provider

DagSentry는 Slack Web API `chat.postMessage`로 알림을 보냅니다. 같은 어댑터가 Diagnosis, Incident 복구,
일일 리포트 payload를 처리하며 Core는 Provider 중립 계약만 의존합니다.

## Slack 앱 설정

Slack 앱을 만들고 bot token에 `chat:write` scope를 부여한 뒤 대상 channel에 bot을 초대합니다.

```text
DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=environment
DAGSENTRY_NOTIFICATION_PROVIDER=slack
DAGSENTRY_SLACK_BOT_TOKEN=xoxb-...
DAGSENTRY_SLACK_CHANNEL=C0123456789
```

또는 대상 환경에 활성 Slack Managed Connection을 만들고 database source를 선택합니다.

```text
DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=database
DAGSENTRY_CONNECTION_ENCRYPTION_KEY=replace-with-the-generated-value
DAGSENTRY_CONNECTION_ENCRYPTION_KEY_VERSION=1
```

database mode는 암호화한 bot token과 channel, API URL, timeout, retry 설정을 읽으며 Slack 환경값으로
fallback하지 않습니다. Diagnosis Worker는 Failure Event 환경별로 job 하나당 snapshot 하나를 해석하고,
Recovery Checker와 Daily Report도 구성 환경/실행별 snapshot을 해석합니다. 연결 변경은 restart 없이 다음
job/run부터 적용됩니다. database-backed Notification 선택은 현재 Slack만 지원합니다.

Slack은 더 이상 권장하지 않는 channel name 대신 channel-like ID를 권장합니다. bot을 초대하지 않고 public
channel에 게시하려면 `chat:write.public`도 필요합니다. token은 `Authorization: Bearer` header로만 보내고
설정 표시에서는 마스킹합니다.

## 메시지와 전송 동작

요청에는 접근 가능한 top-level fallback text, plain-text Block Kit section, message metadata가 들어갑니다.
metadata에는 알림 유형, resource ID, 안정적인 delivery key를 기록하며 link unfurling은 비활성화합니다.
긴 필드는 Slack text/Block Kit 제한에서 잘라내고 전체 source payload는 DagSentry delivery record에 남깁니다.

Slack은 HTTP 200으로 API 오류를 낼 수 있으므로 HTTP status와 JSON `ok`를 모두 검사합니다. 인증, 권한,
rate limit, unavailable, invalid request를 중립 오류 분류로 매핑합니다. `408`, `429`, `5xx`, timeout,
network failure, Slack transient error는 제한해 재시도하며 429의 유효 `Retry-After`는 최대 60초까지 따릅니다.

## 검증

```shell
uv run pytest tests/test_slack_provider.py tests/test_runtime_connections.py
```

영문 원문: [Slack Notification Provider](slack-provider.md)
