# Provider extension API v1

`dagsentry.extensions` is the supported import surface for external Diagnosis LLM and Notification
adapters beginning with DagSentry 0.3. `PROVIDER_API_VERSION == 1` identifies the contract major.
The API formalizes the boundaries shared by existing built-in Providers; vendor SDK types stay
inside adapters.

| Interface | Required behavior |
| --- | --- |
| `LLMProvider.diagnose(request)` | Accept `AIDiagnosisRequest`; return `LLMResult` with a validated `AIDiagnosisResponse` and non-secret `LLMCallMetadata`, or raise sanitized `LLMProviderError`. |
| `NotificationProvider.name` | A stable, non-empty provider name stored with delivery attempts. |
| `NotificationProvider.send(payload, *, delivery_key)` | Accept `NotificationMessage` (Diagnosis, Recovery or Daily Report), retain the delivery key and return the transport success status; raise `NotificationProviderError` with a stable category and retry decision on failure. |

The facade exports context types, payloads, enums, errors and common Diagnosis instructions.
Payload/schema and prompt versions remain separate from the Provider API major. Never add vendor
branches to Core, send full raw logs or bypass Evidence Validation. Bound timeouts and retries;
never include credentials, response bodies or transport exception details in errors or telemetry.
A delivery key provides traceability and receiver deduplication; SMTP and receivers without
idempotency support cannot promise exactly-once external delivery.

## Compatibility

Within API major 1, exported imports, method signatures, required constructor fields and documented
field meanings remain compatible. Adding required methods/fields, removing exports, narrowing
accepted inputs or changing meanings requires a new Provider API major. Internal modules, private
helpers, ORM models and vendor configuration classes are outside this guarantee. Prompt content
may evolve under its own prompt version; payload changes follow their schema version.

External packages should declare their tested DagSentry version range, check
`PROVIDER_API_VERSION`, and run contract tests against every supported version. A new Provider API
major does not immediately retire the old one: the [deprecation policy](version-compatibility.md)
requires a notice window and coexistence tests.

## External examples and tests

[`gateway.py`](../examples/providers/gateway.py) implements both interfaces for an illustrative
company JSON gateway using only the public facade. Its HTTPS endpoints are placeholders and its
request/response format is specific to the example. Supply credentials through the host
application's Secret mechanism. [`test_gateway.py`](../examples/providers/test_gateway.py) runs
entirely with HTTP mocks.

```sh
python -m pip install 'dagsentry[provider-tests]'
python -m pytest examples/providers/test_gateway.py -q
```

The harnesses ship in the wheel, so external repositories do not import DagSentry's `tests/`:

```python
from dagsentry.extensions import LLMProvider, NotificationProvider, PROVIDER_API_VERSION
from dagsentry.extensions.testing.llm import LLMProviderContract
from dagsentry.extensions.testing.notification import NotificationProviderContract
```

Implement the three LLM hooks or the two HTTP Notification hooks described in the
[LLM contract](llm-provider-contract.md) and [Notification contract](notification-provider-contract.md).
The latter checks HTTP retry/status behavior; non-HTTP transports supply equivalent transport-specific
tests. Test Recovery and Daily Report rendering as well. Built-ins use the same shipped harnesses.

Host Python composition code injects adapters into `DiagnosisPipeline` (`llm_provider`,
`notification_provider`), `DailyReportService` or `RecoveryChecker`. These application constructors
are internal composition points, outside the versioned Provider facade. Environment selectors and
Managed Connections select built-ins; dynamic discovery or automatic package loading is not
provided. `DiagnosisContextProvider` remains outside the API until a concrete use case exists.
