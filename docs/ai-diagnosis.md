# LLM Provider and AI Diagnosis

DagSentry's Core depends only on the `LLMProvider` protocol. OpenAI, Azure OpenAI, Anthropic, AWS
Bedrock, and Ollama adapters implement the contract; Provider-specific request fields and response
parsing remain under `dagsentry.providers`.

AI Diagnosis is disabled by default. In that mode, or whenever the Provider fails, the effective
result is the deterministic Rule Diagnosis. An empty Relevant Log Excerpt always skips the
Provider because DagSentry does not ask an LLM to infer a root cause without log evidence.

## Request boundary

The Provider receives a typed allowlist containing only:

- Failure metadata
- Rule Diagnosis
- Error Signature
- sanitized Relevant Log Excerpt lines
- an optional previously validated Diagnosis

There is no request field for a full raw Task log or Incident history. The OpenAI adapter also
sets `store` to `false` and requests a strict `json_schema` Structured Output through
`text.format`.

The returned schema contains classification, Root Cause, confidence, Evidence, recommended
actions, retry decision, and whether operator review is required. JSON shape and Enum values are
validated at the Provider boundary. Evidence line identity, exact text, and Secret re-exposure are
then validated before an AI Diagnosis can become `PASSED`; see
[`evidence-validation.md`](evidence-validation.md).

## Configuration

Enable the OpenAI Provider explicitly:

```dotenv
DAGSENTRY_LLM_PROVIDER=openai
DAGSENTRY_LLM_MODEL=gpt-5.6-luna
DAGSENTRY_LLM_PROMPT_VERSION=ai-diagnosis-v1
DAGSENTRY_OPENAI_API_KEY=replace-with-an-openai-api-key
```

Timeout, maximum attempts, retry backoff, and the API base URL are also configurable; see
`.env.example`. HTTP 429, 5xx, timeout, and transport failures are retried only up to the configured
attempt count. Other 4xx responses are not retried.

Successful call logs contain only request ID, model, prompt version, latency, and input/output
token counts. Failure logs contain a sanitized category and never include the API key, request
payload, or response body.
