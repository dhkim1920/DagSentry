# Anthropic Provider

DagSentry supports structured Diagnosis through Anthropic's Messages API. The adapter uses native
JSON structured outputs and maps the response into the same `LLMResult` consumed by Diagnosis Core.

## Setup

Choose a model that supports structured outputs and configure:

```text
DAGSENTRY_LLM_PROVIDER=anthropic
DAGSENTRY_LLM_MODEL=claude-sonnet-4-6
DAGSENTRY_ANTHROPIC_API_KEY=...
DAGSENTRY_ANTHROPIC_MAX_OUTPUT_TOKENS=2048
```

Requests are sent to `POST /v1/messages` with the API key in `x-api-key` and the required
`anthropic-version: 2023-06-01` header. The key is never included in payloads, logs, or errors and is
masked in configuration representations.

## Structured output and fallback

The request supplies `AIDiagnosisResponse.model_json_schema()` through
`output_config.format.type=json_schema`. Only the bounded `AIDiagnosisRequest` JSON is placed in the
user message. Anthropic returns the structured JSON in a text content block, which DagSentry parses
again with Pydantic before application-side exact Evidence validation.

Anthropic can return HTTP 200 with `stop_reason=refusal` or `stop_reason=max_tokens`; these responses
may not match the requested schema and are rejected. Invalid or incomplete output raises a sanitized
`LLMProviderError`, so the existing deterministic Rule Diagnosis fallback remains available.

`408`, `429`, all `5xx` responses including overload, timeouts, and network failures receive bounded
retries. A valid `retry-after` header is honored with a 60-second cap. Permanent HTTP errors are not
retried. The adapter stores only neutral request ID, model, prompt version, latency, and token usage
metadata.

Anthropic documents that prompts and outputs qualify for its structured-output retention behavior,
while the JSON schema itself may be cached temporarily for grammar compilation. The schema contains
only DagSentry field definitions and no Failure Event data.

The Anthropic adapter currently applies to Diagnosis. Daily report AI prose remains direct-OpenAI
only; Anthropic selection still generates and sends the full deterministic daily report.

## Verification

```shell
uv run pytest tests/test_anthropic_provider.py
```

Anthropic references:

- [Messages API](https://platform.claude.com/docs/en/api/messages/create)
- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [API errors](https://platform.claude.com/docs/en/api/errors)
