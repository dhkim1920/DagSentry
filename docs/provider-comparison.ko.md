# Provider 비교

DagSentry LLM Provider는 OpenAI, Azure OpenAI, Anthropic, AWS Bedrock, Ollama이며 모두 구조화된 Diagnosis와
공통 fallback 정책을 제공합니다. Notification Provider는 generic Webhook, Slack, Teams, Discord, SMTP입니다.
Provider 변경은 Core의 중립 계약을 바꾸지 않습니다.

각 LLM은 provider별 인증, endpoint/SDK, 구조화 출력 방식, token metadata가 다르지만 JSON schema 검증,
Evidence 정확성 검증, 제한 retry, secret 미노출 원칙은 같습니다. OpenAI만 현재 일일 리포트의 선택적 AI
narrative를 제공합니다. Notification Provider는 delivery payload를 각 벤더 형식으로 렌더링합니다. Slack은
Block Kit, Teams는 Adaptive Card, Discord는 embed, SMTP는 plain-text email, generic Webhook은 중립 JSON을
사용합니다.

설정 source는 environment가 기본이고, database Managed Connection은 현재 Airflow/Ollama/Slack에만 적용됩니다.
각 Provider의 인증, timeout/retry, structured output, rate limit, 운영 제약은 개별 Provider 문서를 참조하세요.

영문 원문: [Provider comparison](provider-comparison.md)
