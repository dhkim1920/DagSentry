# LLM Provider와 AI 진단

다섯 Provider는 공통 `ai-diagnosis-ko-v1` 프롬프트를 사용합니다. 원인 설명과 조치는 한국어로,
근거는 공백을 포함한 발췌 원문 그대로 요청합니다. 근거 검증은 인용 일치 검사이며 원인 판단 전체의
정확성을 보증하지 않습니다.

DagSentry Core는 `LLMProvider` 프로토콜에만 의존합니다. OpenAI, Azure OpenAI, Anthropic, AWS
Bedrock, Ollama 어댑터가 이 계약을 구현하며, Provider별 요청 필드와 응답 파싱은
`dagsentry.providers`에 남아 있습니다.

AI 진단은 기본으로 비활성화됩니다. 이 모드이거나 Provider 호출에 실패하면 결정적인 Rule Diagnosis가
최종 결과가 됩니다. 관련 로그 발췌문이 비어 있으면 로그 근거 없이 원인을 추론하도록 LLM에 요청하지
않으므로 Provider 호출을 항상 건너뜁니다.

## 요청 경계

Provider에는 다음 허용 목록만 포함된 타입 요청을 전달합니다.

- 실패 메타데이터
- Rule Diagnosis
- Error Signature
- 비밀값을 마스킹한 관련 로그 발췌문 줄
- 선택적으로, 사전에 검증된 Diagnosis

전체 원본 Task 로그나 Incident 이력은 요청 필드에 포함되지 않습니다. OpenAI 어댑터는 `store`를
`false`로 설정하고 `text.format`으로 엄격한 `json_schema` Structured Output을 요청합니다.

응답 스키마에는 분류, 근본 원인, 신뢰도, 근거, 권장 조치, 재시도 판단, 운영자 검토 필요 여부가
들어갑니다. Provider 경계에서 JSON 형태와 Enum 값을 검증하고, AI Diagnosis를 `PASSED`로 만들기
전 근거 줄의 식별자·정확한 텍스트·비밀값 재노출을 다시 검사합니다. 자세한 내용은
[근거 검증](evidence-validation.ko.md)을 참조하세요.

## 설정

OpenAI Provider를 명시적으로 활성화합니다.

```dotenv
DAGSENTRY_LLM_PROVIDER=openai
DAGSENTRY_LLM_MODEL=gpt-5.6-luna
DAGSENTRY_LLM_PROMPT_VERSION=ai-diagnosis-ko-v1
DAGSENTRY_OPENAI_API_KEY=replace-with-an-openai-api-key
```

타임아웃, 최대 시도 횟수, 재시도 backoff, API base URL도 `.env.example`에서 설정할 수 있습니다.
HTTP 429, 5xx, 타임아웃, 전송 오류만 설정한 횟수까지 재시도하며 다른 4xx 응답은 재시도하지 않습니다.

성공 로그에는 요청 ID, 모델, 프롬프트 버전, 지연 시간, 입출력 토큰 수만 남깁니다. 실패 로그에는
정제한 오류 분류만 남기며 API 키·요청 payload·응답 본문은 포함하지 않습니다.

영문 원문: [LLM Provider and AI Diagnosis](ai-diagnosis.md)
