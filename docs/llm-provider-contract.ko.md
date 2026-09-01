# LLM Provider 계약

DagSentry LLM 계약 테스트는 구조화된 AI Diagnosis에서 Core가 보는 경계를 고정합니다. OpenAI Responses,
Azure OpenAI v1, Anthropic Messages, AWS Bedrock Converse, Ollama Chat이 이를 구현하며, Diagnosis Core에
벤더 분기를 추가하지 않고 같은 테스트 모음을 실행합니다.

## 필수 동작

각 구현은 동일한 `AIDiagnosisRequest` 허용 목록을 정확히 직렬화해 보내고, 엄격한
`AIDiagnosisResponse` 형태를 요청 또는 강제해야 합니다. 잘못된 JSON·알 수 없는 필드·잘못된 enum·
`0..1` 밖의 confidence를 거부하고, Provider 중립 `LLMResult`와 비밀값 없는 `LLMCallMetadata`를
반환합니다. rate limit, 서버 오류, 타임아웃, 네트워크 오류는 제한된 횟수만 재시도하며, credential,
응답 본문, transport 예외 세부 정보를 오류에 복사하지 않습니다.

공통 HTTP endpoint, 인증 방식, 네이티브 구조화 출력, 토큰 사용 필드 이름, 요청 ID 헤더는 계약에
요구하지 않습니다. 구체 어댑터가 이런 차이를 중립 결과로 매핑합니다. Evidence 정확성은 Provider
이후에도 애플리케이션 책임이며 `validate_ai_diagnosis`가 모든 줄 ID와 텍스트가 마스킹된 발췌문에
정확히 속하는지 증명합니다.

## 구현 추가

`tests.contracts.llm.LLMProviderContract`를 상속한 테스트 클래스를 만들고 `make_provider`,
`extract_request_context`, `assert_structured_output_requested`를 구현합니다. 상속 테스트는 성공,
메타데이터, 일시 실패, 재시도 소진, 영구 실패, 잘못된 출력/필드/enum/confidence를 검사합니다.

```shell
uv run pytest tests/test_ollama_provider.py
```

영문 원문: [LLM Provider contract](llm-provider-contract.md)
