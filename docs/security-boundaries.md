# Authentication boundaries

DagSentry uses independently scoped credentials at each network boundary. Credentials come
from environment-backed settings or the platform's identity chain and are never persisted in
DagSentry tables.

## Inbound to DagSentry

| Caller | Target | Authentication | Scope |
| --- | --- | --- | --- |
| Airflow Listener, retry callback, Reconciler | `POST /api/v1/failure-events` | `X-DagSentry-Token` shared secret | Failure ingestion only |
| Viewer/client | Incident, Signature, Diagnosis query APIs | `X-DagSentry-Viewer-Token` shared secret | Read only |
| Operator/client | Query and Incident transition APIs | `X-DagSentry-Operator-Token` shared secret mapped to `DAGSENTRY_OPERATOR_API_IDENTITY` | Read and Incident transitions |
| Health/load balancer | `/health/live`, `/health/ready` | None | Network-restricted health checks |
| Prometheus | `/metrics` | None | Network-restricted metrics scrape |

The Ingest, Viewer, and Operator tokens must be different. Missing Viewer and Operator
configuration disables query APIs rather than allowing anonymous access. An Operator token is not
usable until `DAGSENTRY_OPERATOR_API_IDENTITY` maps it to a server-controlled audit actor. Request
bodies cannot override that actor. Token comparison is constant-time. DagSentry does not issue,
refresh, or centrally manage these tokens.

## Outbound from DagSentry

| Caller | Target | Authentication | Minimum scope |
| --- | --- | --- | --- |
| Worker, Reconciler, Recovery Checker | Airflow Public REST API | Deployment-supplied Bearer token/JWT | Read Task logs, TaskInstances, and Try history needed by the enabled component |
| Diagnosis Worker | OpenAI, Azure OpenAI, Anthropic | Provider API key | Structured Diagnosis request only |
| Diagnosis Worker | AWS Bedrock | Standard AWS credential chain/IAM role | `bedrock:InvokeModel` for the configured model/profile |
| Diagnosis Worker | local/self-hosted Ollama | No built-in local authentication | Private-network `/api/chat` access |
| Notification processes | Generic Webhook | Optional Bearer token | POST to the configured endpoint |
| Notification processes | Slack | Bot token | `chat:write` for the configured channel |
| Notification processes | Microsoft Teams | Secret-bearing Workflows Webhook URL | Execute that Workflow trigger only |
| Notification processes | Discord | Secret-bearing Incoming Webhook URL | Execute that Webhook only |

Airflow token acquisition and rotation depend on the configured Airflow auth manager. DagSentry
accepts the resulting token through `DAGSENTRY_AIRFLOW_API_TOKEN` and sends it only in the
`Authorization: Bearer` header. Provider-specific details are in
[`provider-comparison.md`](provider-comparison.md).

## Deployment rules

- Inject credentials at runtime; do not commit `.env` or bake secrets into images.
- Restrict unauthenticated health and metrics endpoints at the service network boundary.
- Use TLS for traffic that leaves a trusted local network. TLS termination and certificate
  management are deployment responsibilities.
- Keep Ollama on a private network or place an authenticated reverse proxy in front of it.
- Rotate a credential by updating its runtime secret and restarting the affected process. Airflow
  collectors, DagSentry services, and external Providers can be rotated independently.
- Logs, exceptions, metrics, and stored payloads must not include credentials or raw Provider error
  bodies.

Authentication failure, cross-token rejection, and secret masking are covered by the Ingest,
Incident API, Airflow client, and Provider contract tests.

Concrete PostgreSQL role grants, remote credential scopes, and pre-deployment, post-deployment,
and recurring operator checks are documented in
[`security-checklist.md`](security-checklist.md).
