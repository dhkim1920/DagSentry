# Azure OpenAI Provider

DagSentry는 Azure OpenAI v1 Responses API로 구조화된 진단을 지원합니다. 이 어댑터는 OpenAI
Provider와 요청 직렬화, 엄격한 JSON Schema 출력, 재시도, 응답 파싱, 중립 호출 메타데이터를 공유하며
Azure endpoint와 인증 차이만 담당합니다.

## 설정

Azure OpenAI에 Responses 지원 모델을 배포한 다음 설정합니다.

```text
DAGSENTRY_LLM_PROVIDER=azure_openai
DAGSENTRY_LLM_MODEL=orders-diagnosis-deployment
DAGSENTRY_AZURE_OPENAI_ENDPOINT=https://resource-name.openai.azure.com
DAGSENTRY_AZURE_OPENAI_API_KEY=...
```

`DAGSENTRY_LLM_MODEL`은 Azure 배포 이름입니다. DagSentry는 리소스 루트나 `/openai/v1`로 끝나는
endpoint를 정규화한 뒤 `/responses`로 요청합니다. endpoint는 HTTPS여야 합니다.

API 키는 Azure의 `api-key` 헤더로만 전송하며 요청 본문·로그·오류에 넣지 않고 설정 표시에서도
마스킹합니다. 이 초기 어댑터는 API 키 인증만 지원하며 Microsoft Entra ID 토큰 획득은 포함하지 않습니다.

## 공통 동작

Azure 응답은 직접 OpenAI와 동일한 `AIDiagnosisResponse` 검증 및 애플리케이션 측 정확한 Evidence
검증을 거칩니다. Rate limit, `408`, `5xx`, 타임아웃, 네트워크 오류는 설정한 횟수로 제한해 재시도합니다.
영구 HTTP 오류와 잘못된 구조화 출력은 정제한 `LLMProviderError`가 되어 Rule Diagnosis fallback이
실행됩니다.

Azure 어댑터는 현재 Diagnosis에만 적용됩니다. 일일 리포트 AI 문장은 직접 OpenAI Provider에서만
활성화되며 Azure를 선택해도 AI 문장 없이 결정적 일일 리포트 전체를 생성·전송합니다.

## 검증

```shell
uv run pytest tests/test_azure_openai_provider.py
```

영문 원문: [Azure OpenAI Provider](azure-openai-provider.md)
