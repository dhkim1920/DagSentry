# 인증 경계

DagSentry는 네트워크 경계마다 독립적인 scope의 credential을 사용합니다. credential은 환경 설정 또는 플랫폼
identity chain에서 오며 DagSentry table에 영속화하지 않습니다.

Inbound에서 Airflow Listener/retry callback/Reconciler는 `X-DagSentry-Token`으로 Failure Ingest만 호출합니다.
Viewer는 `X-DagSentry-Viewer-Token`으로 조회만, Operator는 `X-DagSentry-Operator-Token`과
`DAGSENTRY_OPERATOR_API_IDENTITY`로 조회와 Incident 전이를 수행합니다. `/health/live`, `/health/ready`,
`/metrics`는 인증하지 않으므로 network 제한이 필수입니다. Ingest, Viewer, Operator token은 모두 달라야 하며
Viewer/Operator 설정이 없으면 anonymous access가 아니라 query API를 비활성화합니다.

Outbound에서 Worker/Reconciler/Recovery Checker는 최소 필요한 Airflow Public API 읽기 권한의 Bearer token을,
LLM Worker는 Provider API key 또는 Bedrock IAM role을, local Ollama에는 private `/api/chat` 접근을 사용합니다.
알림에는 generic Webhook Bearer token(선택), Slack bot `chat:write`, Teams/Discord secret webhook URL을 씁니다.
SMTP 알림은 SMTP server credential과 TLS 설정을 사용합니다. credential은 runtime에 주입하고 `.env`를 commit하거나
image에 secret을 bake하지 않습니다. trusted local network 밖 traffic은 TLS를 쓰고, Ollama는 private network 또는
authenticated reverse proxy 뒤에 둡니다. log, exception, metric, 저장 payload에 credential이나 raw Provider error
body를 넣지 않습니다.

영문 원문: [Authentication boundaries](security-boundaries.md)
