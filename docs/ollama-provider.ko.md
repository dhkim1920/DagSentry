# Ollama Provider

DagSentry는 Ollama 네이티브 Chat API로 구조화된 Diagnosis를 지원합니다. 이 어댑터는 로컬 또는 자체 호스팅
Ollama 서버용이며 Ollama SDK 의존성을 추가하지 않습니다.

## 설정

Ollama를 설치하고 구조화 출력을 지원하는 모델을 pull한 뒤 API를 실행합니다.

```shell
ollama pull gemma3
```

```text
DAGSENTRY_LLM_CONFIG_SOURCE=environment
DAGSENTRY_LLM_PROVIDER=ollama
DAGSENTRY_LLM_MODEL=gemma3
DAGSENTRY_OLLAMA_API_BASE_URL=http://localhost:11434/api
DAGSENTRY_OLLAMA_MAX_OUTPUT_TOKENS=2048
```

또는 Failure Event와 같은 환경에 활성 Ollama Managed Connection을 만들고
`DAGSENTRY_LLM_CONFIG_SOURCE=database`를 선택합니다. database mode는 매 Diagnosis 시작 시 API URL,
모델, 출력 제한, timeout, retry 설정을 읽으며 Worker restart 없이 다음 job부터 변경이 적용됩니다. 연결이
없거나 비활성/잘못됨/아직 전환하지 않은 Provider면 환경 Ollama 값으로 fallback하지 않고 job은
`llm_connection` 단계에서 재시도 가능합니다. 유효 연결의 외부 요청 실패는 기존 결정적 Rule fallback을
따릅니다.

DagSentry와 Ollama가 별도 container에 있으면 `http://ollama:11434/api`처럼 도달 가능한 hostname을
사용합니다. DagSentry가 `/chat`을 덧붙이므로 `/api` suffix를 유지하세요. 로컬 API는 인증이 없으므로
신뢰할 수 없는 network에 직접 노출하지 말고 private network 또는 인증 reverse proxy 뒤에 둡니다.
Ollama Cloud는 현재 구조화 출력을 지원하지 않아 이 어댑터 범위 밖입니다.

## 구조화 출력과 검증

어댑터는 제한된 Diagnosis context를 `POST /api/chat`에 보내고 `format`에 완전한 Pydantic
`AIDiagnosisResponse` JSON Schema, 원자 응답 파싱을 위한 `stream=false`, 결정적 출력을 위한
`temperature=0`, `DAGSENTRY_OLLAMA_MAX_OUTPUT_TOKENS`의 `num_predict`를 설정합니다. 반환
`message.content`는 완전한 JSON이고 Pydantic 모델을 통과해야 합니다. 기존 Evidence 검증이 모든 줄 ID와
텍스트가 마스킹된 excerpt의 정확한 구성원임을 증명합니다. malformed, incomplete, schema-invalid 출력은
일반 Rule Diagnosis 경로로 fallback합니다.

timeout, network failure, HTTP 408/429/5xx는 공통 제한 재시도 정책을 적용하며 다른 4xx는 재시도하지
않습니다. Ollama는 request ID를 주지 않으므로 중립 메타데이터에는 모델, prompt version, 측정 latency,
`prompt_eval_count`, `eval_count`만 넣습니다. 응답 본문과 transport 예외 세부 정보는 로그나 Provider
오류에 복사하지 않습니다. 이 어댑터는 현재 Diagnosis에만 적용되며 일일 리포트 AI 문장은 OpenAI만 지원합니다.

## 검증

```shell
uv run pytest tests/test_ollama_provider.py
```

영문 원문: [Ollama Provider](ollama-provider.md)
