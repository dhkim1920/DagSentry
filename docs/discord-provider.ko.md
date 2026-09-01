# Discord Notification Provider

DagSentry는 Discord Incoming Webhook으로 Diagnosis, Incident 복구, 일일 리포트 메시지를 전송합니다.
Incoming Webhook은 channel별 HTTP endpoint이므로 bot 사용자나 영구 연결이 필요하지 않습니다.

## 설정

대상 Discord channel에 Incoming Webhook을 만들고 URL을 복사해 설정합니다.

```text
DAGSENTRY_NOTIFICATION_PROVIDER=discord
DAGSENTRY_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/id/token
```

URL에는 secret token이 포함됩니다. DagSentry는 이를 로그나 오류에 넣지 않고 설정 표시에서 전체 URL을
마스킹합니다.

## 메시지와 전송 동작

Provider는 접근 가능한 fallback `content`와 rich embed 하나를 보냅니다. Diagnosis에는 실패 식별자,
Incident 상태, classification, Evidence, 권장 조치, signature, Airflow log URL이 들어가며 복구와 일일
리포트도 같은 어댑터를 사용합니다. 모든 요청은 `allowed_mentions.parse=[]`로 자동 mention을 끄므로 DAG
이름, 오류, AI 문장이 Discord 사용자나 role을 예기치 않게 ping할 수 없습니다.

`wait=true`는 fire-and-forget 수락이 아니라 메시지 저장과 생성 메시지 반환을 기다리도록 Discord에
요청합니다. 안정적인 delivery key는 수신자 추적용 embed footer에 포함합니다. Discord는 강한 멱등성 API를
문서화하지 않았으므로 수락 뒤 모호한 timeout은 재시도 중복을 낼 수 있지만 영속 delivery 상태가 일반적
반복 전송을 막습니다.

`408`, `429`, `5xx`, timeout, network failure는 제한된 횟수로 재시도합니다. HTTP 429에서는 먼저
Discord `Retry-After`, 그 다음 JSON `retry_after`를 최대 60초까지 따릅니다. 인증, 권한, invalid request,
transient failure는 공통 Notification Provider의 중립 분류로 매핑합니다.

## 검증

```shell
uv run pytest tests/test_discord_provider.py
```

영문 원문: [Discord Notification Provider](discord-provider.md)
