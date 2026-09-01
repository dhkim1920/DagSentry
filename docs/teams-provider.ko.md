# Microsoft Teams Notification Provider

DagSentry는 retiring Office 365 Connector Webhook 대신 Power Automate Workflow Webhook으로 Microsoft
Teams에 Diagnosis, Incident 복구, 일일 리포트 알림을 보냅니다.

## 설정

대상 Teams 채널에서 `When a Teams webhook request is received` trigger와 `Post card in a chat or channel`
action이 있는 Workflow를 만듭니다. DagSentry 서비스 요청을 허용하는 인증 옵션을 선택하고 trigger URL을
복사해 설정합니다.

```text
DAGSENTRY_NOTIFICATION_PROVIDER=teams
DAGSENTRY_TEAMS_WEBHOOK_URL=https://example.logic.azure.com/workflows/.../triggers/manual/paths/invoke?sig=...
```

URL에는 secret signature가 있습니다. DagSentry는 요청 대상으로만 보내고 오류에서 제외하며 Provider 설정
표시에서는 전체 URL을 마스킹합니다. 다른 credential처럼 저장·회전하세요. Workflow는 Teams 채널이 아니라
사용자가 소유하므로 적절한 co-owner를 추가하고 offboarding 절차에 소유권 이전을 포함해야 합니다.

## 메시지와 전송 동작

각 요청은 Adaptive Card 1.2 attachment 하나가 든 Teams Workflows message envelope입니다. Diagnosis card에는
실패 식별자, Incident 상태, 분류, Evidence, 권장 조치, Error Signature, Airflow log URL이 들어갑니다.
복구와 일일 리포트도 같은 어댑터를 사용합니다. Card에는 action이나 Teams mention entity가 없으므로
DagSentry 내용으로 대화형 action 또는 명시적 Teams mention을 만들 수 없습니다.

안정적인 delivery key는 수신자 추적용 footer에 표시합니다. Teams Workflows는 DagSentry idempotency-key
계약을 제공하지 않으므로 수신 뒤 모호한 timeout은 재시도 중복을 낼 수 있지만 영속 delivery 상태가
일반적인 반복 전송을 막습니다. Provider는 모든 `2xx`를 받고 `408`, `429`, `5xx`, timeout, network failure를
제한해 재시도합니다. `429`에서는 최대 60초인 숫자 `Retry-After`를 따르며 그 외에는 구성 fallback delay를
사용합니다.

## 검증

```shell
uv run pytest tests/test_teams_provider.py
```

영문 원문: [Microsoft Teams Notification Provider](teams-provider.md)
