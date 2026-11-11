# LLM Provider와 AI 진단

다섯 Provider는 공통 `ai-diagnosis-ko-v2` 프롬프트를 사용합니다. 원인 설명과 조치는 한국어로,
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
DAGSENTRY_LLM_PROMPT_VERSION=ai-diagnosis-ko-v2
DAGSENTRY_OPENAI_API_KEY=replace-with-an-openai-api-key
```

타임아웃, 최대 시도 횟수, 재시도 backoff, API base URL도 `.env.example`에서 설정할 수 있습니다.
HTTP 429, 5xx, 타임아웃, 전송 오류만 설정한 횟수까지 재시도하며 다른 4xx 응답은 재시도하지 않습니다.

성공 로그에는 요청 ID, 모델, 프롬프트 버전, 지연 시간, 입출력 토큰 수만 남깁니다. 실패 로그에는
정제한 오류 분류만 남기며 API 키·요청 payload·응답 본문은 포함하지 않습니다.

영문 원문: [LLM Provider and AI Diagnosis](ai-diagnosis.md)

## 근거 수준

새 진단은 `assessment`에 근거 수준, 판단 이유, 확인된 사실, 원인 후보, 부족한 정보, 다음 확인을
저장합니다. 화면과 알림은 퍼센트 대신 직접 근거 있음(`DIRECT`), 원인 추정(`INFERRED`),
판단 불가(`UNDETERMINED`)를 표시합니다. 이 수준은 진단이 제시한 결론의 근거에 대한 평가이며,
운영자가 원인을 확정했거나 실제 정확도를 측정했다는 의미가 아닙니다.

401은 인증 오류의 근거이지만 토큰 만료를 입증하지 않습니다. 메모리 부족 신호도 메모리 누수를
입증하지 않습니다. 규칙 진단의 직접 근거는 이처럼 로그에 명시된 오류 유형에만 적용됩니다.
AI는 구체적인 원인을 추정할 때 대안과 필요한 확인 정보를 함께 제시합니다.

판단 불가 응답은 근거가 비어 있어도 허용하고 운영자 검토를 요구합니다. 제공한 인용이 잘못됐거나
새 설명 필드에서 비밀값이 재노출되면 기존처럼 거부합니다. 로그가 아예 없으면 운영 파이프라인은
AI를 호출하지 않고 판단 불가 규칙 결과를 사용합니다. 인용 일치 검사는 설명의 의미까지 검증하지
않으므로 별도의 실제 모델 평가가 필요합니다.

마이그레이션 `0023`은 기존 행의 `assessment`를 채우지 않습니다. 기존 진단은 근거 수준 미평가로
표시되며, 재사용 행은 원본의 평가를 표시합니다. 새 schema v2와 ruleset v3은 이전 결과의 자동
재사용을 막습니다. `confidence`는 기존 API와 저장 계약을 위해 남겨두며 근거 수준 산정에 쓰지
않습니다.

대표 합성 장애 사례는 `evals/diagnosis/cases.json`에 있습니다. 모델을 호출하지 않는 테스트와
실제 모델 평가는 구분합니다. 반복 평가 절차는
[진단 평가 스킬](../.agents/skills/evaluate-diagnosis/SKILL.md)에 있습니다.
