# AWS Bedrock Provider

DagSentry supports structured Diagnosis through the Amazon Bedrock Runtime Converse API. Converse
provides a normalized message interface and supports JSON Schema structured output for compatible
models.

## Setup

Enable access to a structured-output-capable model and grant the service identity
`bedrock:InvokeModel` for the selected model or inference profile. Configure:

```text
DAGSENTRY_LLM_PROVIDER=bedrock
DAGSENTRY_LLM_MODEL=anthropic.claude-sonnet-4-6-v1:0
DAGSENTRY_BEDROCK_REGION=ap-northeast-2
DAGSENTRY_BEDROCK_MAX_OUTPUT_TOKENS=2048
```

`DAGSENTRY_LLM_MODEL` is passed as the Converse `modelId`, so it may be a foundation model ID,
inference profile ID, or supported ARN. The exact identifier and regional availability depend on
the AWS account and enabled model access.

DagSentry uses boto3's standard AWS credential chain: explicit AWS environment variables, shared
AWS configuration/profile, SSO or assumed role credentials, ECS task role, or EC2 instance role.
AWS access keys are not part of DagSentry `Settings` and are never stored in its database.

## Structured output and validation

The adapter sends the bounded request JSON in a Converse user content block and uses
`outputConfig.textFormat.type=json_schema`. Amazon Bedrock's supported JSON Schema subset excludes
numeric limits and string-length limits, so the transport schema removes only those unsupported
grammar keywords. The returned JSON is always validated again with the complete Pydantic
`AIDiagnosisResponse`, restoring confidence, Evidence line ID, and non-empty string constraints
before the existing exact Evidence validation runs.

AWS can take additional time to compile a new structured-output grammar. Bedrock caches a compiled
schema, so later requests normally avoid that first-use cost.

DagSentry rejects incomplete, guardrail-intervened, filtered, or malformed responses. Throttling,
model-not-ready, service unavailable, model timeout, other transient service errors, and SDK
transport errors receive bounded retries. Missing credentials, access denial, validation errors,
and unavailable model IDs fail without retry and allow the normal Rule Diagnosis fallback.

The boto3 client's internal retries are disabled so there is one observable retry budget controlled
by DagSentry. Neutral request ID, model ID, prompt version, latency, and token counts are returned to
Core; AWS exception messages are not copied into `LLMProviderError`.

The Bedrock adapter currently applies to Diagnosis. Daily report AI prose remains direct-OpenAI
only; Bedrock selection still generates and sends the complete deterministic daily report.

## Verification

```shell
uv run pytest tests/test_bedrock_provider.py
```

AWS references:

- [Structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html)
- [Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
- [Boto3 Converse](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-runtime/client/converse.html)
