# Provider comparison and switching

DagSentry keeps vendor behavior in adapters selected at the composition boundary. Diagnosis,
Incident, recovery, and reporting Core consume provider-neutral contracts.

## LLM Providers

All LLM adapters receive the same bounded `AIDiagnosisRequest`, return `LLMResult`, and pass the
shared contract suite. The default timeout is 15 seconds, with at most two total attempts and a
0.5-second fallback delay. A permanent failure or exhausted retry returns the normal Rule Diagnosis
fallback.

| Provider | Authentication | Structured output | Rate limit and transient retry |
| --- | --- | --- | --- |
| OpenAI | Bearer API key | Responses `text.format` with strict JSON Schema; `store=false` | HTTP 408, 429, 5xx, timeout, and transport errors; configured fixed delay |
| Azure OpenAI | Azure `api-key`; `DAGSENTRY_LLM_MODEL` is the deployment name | Azure v1 Responses with the same strict schema as OpenAI | HTTP 408, 429, 5xx, timeout, and transport errors; configured fixed delay |
| Anthropic | `x-api-key` plus `anthropic-version` | Messages `output_config.format` JSON Schema | HTTP 408, 429, 5xx, timeout, and transport errors; `Retry-After` when valid, capped at 60 seconds |
| AWS Bedrock | boto3 standard credential chain and IAM `bedrock:InvokeModel` | Converse `outputConfig.textFormat`; unsupported numeric/string constraints are removed for transport and restored by full Pydantic validation | AWS throttling, quota, model-not-ready/timeout, service, and SDK transport errors; configured fixed delay; SDK retries disabled |
| Ollama | None for the local API | Chat `format` with the full JSON Schema and `stream=false` | HTTP 408, 429, 5xx, timeout, and transport errors; configured fixed delay |

Ollama support targets local or self-hosted servers. Direct Ollama Cloud access is not supported by
this adapter because Ollama Cloud currently does not support structured outputs. Bedrock model IDs
and structured-output support depend on the selected region and model. Every adapter validates the
complete Pydantic response after transport-level schema enforcement, then Core performs exact
Evidence line validation.

Only OpenAI currently supplies optional daily-report AI prose. Selecting Azure OpenAI, Anthropic,
Bedrock, or Ollama still produces and sends the complete deterministic daily report without AI
prose.

Provider-specific setup details:

- [OpenAI Diagnosis](ai-diagnosis.md)
- [Azure OpenAI](azure-openai-provider.md)
- [Anthropic](anthropic-provider.md)
- [AWS Bedrock](bedrock-provider.md)
- [Ollama](ollama-provider.md)

## Notification Providers

All notification adapters send Diagnosis, Incident recovery, and daily-report payloads through the
same contract. The default timeout is 5 seconds, with at most two total attempts and a 0.5-second
fallback delay. Core persists delivery state and a stable delivery key before calling an adapter.

| Provider | Authentication | Payload and duplicate protection | Rate limit and transient retry |
| --- | --- | --- | --- |
| Generic Webhook | Optional Bearer token | Provider-neutral JSON; delivery key in `Idempotency-Key` | HTTP 408, 429, 5xx, timeout, and transport errors; configured fixed delay |
| Slack | Bot token with `chat:write` and a target channel | `chat.postMessage` Block Kit; delivery key in message metadata | HTTP/application 429 and transient Slack errors; `Retry-After` when valid, capped at 60 seconds |
| Microsoft Teams | Secret-bearing Workflows Webhook URL | Non-interactive Adaptive Card; delivery key in a subtle footer | HTTP 408, 429, 5xx, timeout, and transport errors; `Retry-After` when valid, capped at 60 seconds |
| Discord | Secret-bearing Incoming Webhook URL | Mention-safe embed; `wait=true`; delivery key in the footer | HTTP 408, 429, 5xx, timeout, and transport errors; header/body retry delay when valid, capped at 60 seconds |

Provider-specific setup details:

- [Generic Webhook](notification.md)
- [Slack](slack-provider.md)
- [Microsoft Teams](teams-provider.md)
- [Discord](discord-provider.md)

## Switching without a Core migration

Environment-backed implementations are selected through `DAGSENTRY_LLM_PROVIDER` and
`DAGSENTRY_NOTIFICATION_PROVIDER` plus their credentials and endpoint settings. Ollama Diagnosis
and Slack Notification can instead select enabled environment-specific Managed Connections with
`DAGSENTRY_LLM_CONFIG_SOURCE=database` and
`DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=database`. The runtime composition boundary constructs the
configured adapter; Core receives only `LLMProvider` or `NotificationProviderAdapter`.

No database enum or vendor-specific table controls selection. Persisted notification and report
provider names use generic string columns, while Diagnosis stores provider-neutral model, prompt,
schema, and Evidence data. Switching among the implemented Providers therefore requires no
Alembic migration.

Use this operational sequence:

1. Stop the Diagnosis Worker, Recovery Checker, and Daily Report process.
2. Let pending or failed notification deliveries finish with the old Provider. Recovery and daily
   report delivery records deliberately reject a Provider change while still pending.
3. Change the Provider environment variables and required credentials/endpoints, or update the
   Ollama/Slack Managed Connection used by database source.
4. Restart the processes and confirm startup configuration plus one non-production delivery.

Already delivered rows remain immutable history under their original Provider name. New work uses
the newly selected adapter. Adding a brand-new adapter still requires code, configuration, and the
contract suite, but it does not require a Core database migration while the neutral contracts and
generic persisted fields remain unchanged.

The selection regression tests are in `tests/test_provider_selection.py`; per-adapter behavioral
tests inherit the shared LLM or Notification contract suite.

## References

- [OpenAI structured outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [Azure OpenAI Responses API](https://learn.microsoft.com/azure/foundry/openai/how-to/responses)
- [Azure OpenAI quota and rate limits](https://learn.microsoft.com/azure/foundry/openai/how-to/quota)
- [Anthropic structured outputs](https://docs.anthropic.com/en/docs/build-with-claude/structured-outputs)
- [Amazon Bedrock structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Slack `chat.postMessage`](https://api.slack.com/methods/chat.postMessage)
- [Microsoft Teams Webhooks and connectors](https://learn.microsoft.com/microsoftteams/platform/webhooks-and-connectors/how-to/connectors-using)
- [Discord webhooks](https://discord.com/developers/docs/resources/webhook)
