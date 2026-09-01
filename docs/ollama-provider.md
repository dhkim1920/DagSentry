# Ollama Provider

DagSentry supports structured Diagnosis through Ollama's native Chat API. This adapter targets a
local or self-hosted Ollama server and does not add an Ollama SDK dependency.

## Setup

Install Ollama, pull a model that supports structured output, and make sure its API is running. For
example:

```shell
ollama pull gemma3
```

Configure DagSentry:

```text
DAGSENTRY_LLM_CONFIG_SOURCE=environment
DAGSENTRY_LLM_PROVIDER=ollama
DAGSENTRY_LLM_MODEL=gemma3
DAGSENTRY_OLLAMA_API_BASE_URL=http://localhost:11434/api
DAGSENTRY_OLLAMA_MAX_OUTPUT_TOKENS=2048
```

Alternatively, create an enabled Ollama Managed Connection for the same environment as the Failure
Events and select the database source:

```text
DAGSENTRY_LLM_CONFIG_SOURCE=database
```

Database mode reads the connection's API URL, model, output limit, timeout, and retry settings when
each new diagnosis starts. Updates apply to the next job without restarting the Worker. It does not
fall back to `DAGSENTRY_LLM_PROVIDER`, `DAGSENTRY_LLM_MODEL`, or Ollama environment values when the
connection is missing, disabled, invalid, or configured for a Provider that has not yet been cut
over. Such configuration failures leave the job retryable at the `llm_connection` stage. A valid
Ollama connection whose external request fails still follows the existing deterministic Rule
fallback policy.

When DagSentry and Ollama run in separate containers, use the reachable service hostname, such as
`http://ollama:11434/api`. Keep the `/api` suffix because DagSentry appends `/chat`.

Ollama's local API does not require authentication. Do not expose it directly to an untrusted
network; place it on a private network or behind an authenticated reverse proxy. Direct
`ollama.com` Cloud API access is outside this adapter's scope because Ollama Cloud currently does
not support structured outputs.

## Structured output and validation

The adapter sends the bounded Diagnosis context to `POST /api/chat` with:

- the complete Pydantic `AIDiagnosisResponse` JSON Schema in `format`;
- `stream=false`, so one JSON response is parsed atomically;
- `temperature=0` for deterministic output;
- `num_predict` set from `DAGSENTRY_OLLAMA_MAX_OUTPUT_TOKENS`.

The returned `message.content` must be complete JSON and pass the full Pydantic model. Existing
Evidence validation then proves every line ID and text is an exact member of the sanitized excerpt.
Malformed, incomplete, or schema-invalid output falls back through the normal Rule Diagnosis path.

Timeouts, network failures, HTTP 408/429, and 5xx responses receive the shared bounded retry policy.
Other 4xx responses fail without retry. Ollama does not return a request ID; the neutral metadata
therefore contains the model, prompt version, measured latency, `prompt_eval_count`, and
`eval_count` only. Response bodies and transport exception details are not logged or copied into
Provider errors.

The Ollama adapter currently applies to Diagnosis. Daily report AI prose remains direct-OpenAI
only; Ollama selection still generates and sends the complete deterministic daily report.

## Verification

```shell
uv run pytest tests/test_ollama_provider.py
```

To opt into the live database-source test against a running Ollama server:

```shell
DAGSENTRY_TEST_OLLAMA_API_BASE_URL=http://127.0.0.1:11434/api \
DAGSENTRY_TEST_OLLAMA_MODEL=gemma3:latest \
uv run pytest tests/integration/test_ollama_runtime.py
```

Ollama references:

- [API introduction](https://docs.ollama.com/api/introduction)
- [Chat API](https://docs.ollama.com/api/chat)
- [Structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Authentication](https://docs.ollama.com/api/authentication)
