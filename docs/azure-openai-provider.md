# Azure OpenAI Provider

DagSentry supports structured Diagnosis through the Azure OpenAI v1 Responses API. The adapter
shares request serialization, strict JSON Schema output, retry behavior, response parsing, and
neutral call metadata with the OpenAI Provider. It only owns Azure endpoint and authentication
differences.

## Setup

Deploy a Responses-capable model in Azure OpenAI and configure:

```text
DAGSENTRY_LLM_PROVIDER=azure_openai
DAGSENTRY_LLM_MODEL=orders-diagnosis-deployment
DAGSENTRY_AZURE_OPENAI_ENDPOINT=https://resource-name.openai.azure.com
DAGSENTRY_AZURE_OPENAI_API_KEY=...
```

`DAGSENTRY_LLM_MODEL` is the Azure deployment name. DagSentry normalizes either the resource root or
an endpoint already ending in `/openai/v1`, then posts to `/responses`. Endpoints must use HTTPS.

The API key is sent in Azure's `api-key` header, never in the request body, logs, or errors. The
configuration representation masks it. This initial adapter supports API-key authentication;
Microsoft Entra ID token acquisition is not included.

## Shared behavior

Azure responses pass through the same `AIDiagnosisResponse` validation and application-side exact
Evidence validation as direct OpenAI responses. Rate limits, `408`, `5xx`, timeouts, and network
errors receive the configured bounded retries. Permanent HTTP errors and invalid structured output
produce sanitized `LLMProviderError`, allowing the existing Rule Diagnosis fallback to run.

The Azure adapter currently applies to Diagnosis. Daily report AI prose remains enabled only for
the direct OpenAI Provider; Azure selection still produces and delivers the complete deterministic
daily report without AI.

## Verification

The adapter passes the reusable LLM contract plus Azure-specific endpoint, header, selection, and
credential-masking tests:

```shell
uv run pytest tests/test_azure_openai_provider.py
```

Azure references:

- [Responses REST reference](https://learn.microsoft.com/rest/api/microsoft-foundry/azureopenai/responses)
- [Azure OpenAI Responses guide](https://learn.microsoft.com/azure/foundry/openai/how-to/responses)
