# AWS Bedrock Provider

DagSentry는 Amazon Bedrock Runtime Converse API로 구조화된 Diagnosis를 지원합니다. 구조화 출력을 지원하는
모델 접근을 활성화하고 서비스 identity에 선택한 model 또는 inference profile의 `bedrock:InvokeModel` 권한을
부여합니다.

```text
DAGSENTRY_LLM_PROVIDER=bedrock
DAGSENTRY_LLM_MODEL=anthropic.claude-sonnet-4-6-v1:0
DAGSENTRY_BEDROCK_REGION=ap-northeast-2
DAGSENTRY_BEDROCK_MAX_OUTPUT_TOKENS=2048
```

모델 값은 Converse `modelId`이므로 foundation model ID, inference profile ID, 지원 ARN일 수 있으며 계정의
model access와 regional availability에 따라 달라집니다. boto3 표준 AWS credential chain(환경변수, shared
profile, SSO/assumed role, ECS/EC2 role)을 사용하고 access key는 DagSentry Settings나 DB에 저장하지 않습니다.

어댑터는 제한된 request JSON과 `outputConfig.textFormat.type=json_schema`를 보냅니다. Bedrock JSON Schema
subset이 numeric/string length 제한을 지원하지 않아 transport schema에서는 해당 grammar keyword만 제거합니다.
반환 JSON은 완전한 Pydantic `AIDiagnosisResponse`로 재검증한 뒤 exact Evidence 검증을 적용합니다. incomplete,
guardrail-intervened, filtered, malformed response는 거부합니다. throttling, model-not-ready, service unavailable,
model timeout, transient service/SDK transport error는 제한 재시도하고 credential/access/validation/unavailable
model ID는 재시도하지 않아 Rule Diagnosis fallback이 실행됩니다. boto3 내부 retry는 꺼서 DagSentry의 관측 가능한
retry budget 하나만 사용합니다. Bedrock은 현재 Diagnosis에만 적용되고 일일 리포트 AI 문장은 OpenAI만 지원합니다.

```shell
uv run pytest tests/test_bedrock_provider.py
```

영문 원문: [AWS Bedrock Provider](bedrock-provider.md)
