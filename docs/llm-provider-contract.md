# LLM Provider contract

DagSentry's LLM contract suite fixes the Core-visible boundary for structured AI Diagnosis. OpenAI
Responses, Azure OpenAI v1, Anthropic Messages, AWS Bedrock Converse, and Ollama Chat are conforming
implementations. Each adapter runs the same suite without adding vendor branches to Diagnosis Core.

## Required behavior

Every implementation must:

- accept the same `AIDiagnosisRequest` allowlist and send exactly its serialized fields;
- request or enforce the strict `AIDiagnosisResponse` shape;
- reject malformed JSON, unknown fields, invalid enums, and confidence outside `0..1`;
- return provider-neutral `LLMResult` and non-secret `LLMCallMetadata`;
- retry rate limits, server errors, timeouts, and network failures with bounded attempts;
- avoid copying credentials, response bodies, or transport exception details into raised errors.

The contract intentionally does not require a common HTTP endpoint, authentication scheme, native
structured-output feature, token usage field names, or request ID header. Concrete adapters map
those vendor differences into the neutral result.

Evidence correctness remains an application responsibility after the Provider boundary. A Provider
must return structured Evidence, but `validate_ai_diagnosis` still proves every line ID and text is
an exact member of the sanitized excerpt before the Diagnosis can pass or be reused.

## Adding an implementation

Create a test class derived from `tests.contracts.llm.LLMProviderContract` and implement:

- `make_provider`: translate ordered neutral fake outcomes into the vendor's HTTP or SDK response;
- `extract_request_context`: recover the serialized Core context from the captured request;
- `assert_structured_output_requested`: prove the adapter enforces the shared response schema.

The inherited suite checks success, metadata, every transient failure, retry exhaustion, permanent
failure, malformed output, unknown fields, invalid enums, and invalid confidence.

Run the Ollama implementation:

```shell
uv run pytest tests/test_ollama_provider.py
```
