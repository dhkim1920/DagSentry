# Anthropic Provider

DagSentry는 Anthropic Messages API로 구조화된 진단을 지원합니다. 어댑터는 네이티브 JSON 구조화
출력을 사용하고 응답을 Diagnosis Core가 사용하는 동일한 `LLMResult`로 변환합니다.

## 설정

구조화 출력을 지원하는 모델을 선택해 설정합니다.

```text
DAGSENTRY_LLM_PROVIDER=anthropic
DAGSENTRY_LLM_MODEL=claude-sonnet-4-6
DAGSENTRY_ANTHROPIC_API_KEY=...
DAGSENTRY_ANTHROPIC_MAX_OUTPUT_TOKENS=2048
```

요청은 API 키를 `x-api-key`, 필수 버전 값을 `anthropic-version: 2023-06-01` 헤더에 넣어
`POST /v1/messages`로 보냅니다. 키는 payload·로그·오류에 포함하지 않으며 설정 표시에서도 마스킹합니다.

## 구조화 출력과 fallback

요청은 `output_config.format.type=json_schema`로 `AIDiagnosisResponse.model_json_schema()`를
전달합니다. 사용자 메시지에는 범위가 제한된 `AIDiagnosisRequest` JSON만 들어갑니다. Anthropic이
텍스트 content block으로 반환한 JSON은 Pydantic으로 다시 파싱한 뒤 애플리케이션 측 정확한 Evidence
검증을 거칩니다.

HTTP 200이어도 `stop_reason=refusal` 또는 `stop_reason=max_tokens`일 수 있으며, 이 응답은 요청한
스키마를 만족하지 않을 수 있어 거부합니다. 잘못되었거나 불완전한 출력은 정제된 `LLMProviderError`를
발생시키고 기존의 결정적 Rule Diagnosis fallback을 계속 사용할 수 있습니다.

`408`, `429`, overload를 포함한 모든 `5xx`, 타임아웃, 네트워크 오류는 제한된 횟수만 재시도합니다.
유효한 `retry-after` 헤더는 최대 60초까지 따릅니다. 영구 HTTP 오류는 재시도하지 않으며, 어댑터는
중립적인 요청 ID·모델·프롬프트 버전·지연 시간·토큰 사용 메타데이터만 저장합니다.

Anthropic은 프롬프트와 출력이 구조화 출력 보존 동작의 대상이고 JSON 스키마는 문법 컴파일을 위해
일시적으로 캐시될 수 있다고 문서화합니다. 스키마에는 DagSentry 필드 정의만 있고 Failure Event 데이터는
포함하지 않습니다.

Anthropic 어댑터는 현재 Diagnosis에만 적용됩니다. 일일 리포트의 AI 문장은 직접 OpenAI Provider만
지원하지만 Anthropic을 선택해도 결정적 일일 리포트 전체는 생성·전송됩니다.

## 검증

```shell
uv run pytest tests/test_anthropic_provider.py
```

영문 원문: [Anthropic Provider](anthropic-provider.md)
