# Microsoft Teams Notification Provider

DagSentry sends Diagnosis, Incident recovery, and daily report notifications to Microsoft Teams
through a Power Automate Workflow Webhook. It does not use the retiring Office 365 Connector
Webhook.

## Setup

In the target Teams channel, create a Workflow with the `When a Teams webhook request is received`
trigger and a `Post card in a chat or channel` action. Select the authentication option that accepts
the DagSentry service request, copy the trigger URL, and configure:

```text
DAGSENTRY_NOTIFICATION_PROVIDER=teams
DAGSENTRY_TEAMS_WEBHOOK_URL=https://example.logic.azure.com/workflows/.../triggers/manual/paths/invoke?sig=...
```

The URL contains a secret signature. DagSentry sends it only as the request target, excludes it
from errors, and masks the complete URL in Provider configuration representations. Store and rotate
it like any other credential.

Workflows are owned by users rather than by the Teams channel. Add a suitable co-owner and include
ownership transfer in offboarding procedures so the flow does not become orphaned.

## Message and delivery behavior

Each request uses the Teams Workflows message envelope with one Adaptive Card 1.2 attachment.
Diagnosis cards contain the failure identity, Incident state, classification, Evidence,
recommended actions, Error Signature, and Airflow log URL. Recovery and daily report payloads use
the same adapter. Cards have no actions or Teams mention entities, so DagSentry content cannot
create an interactive action or an explicit Teams mention.

The stable delivery key appears in a subtle footer for receiver-side tracing. Teams Workflows do
not provide a DagSentry idempotency-key contract, so an ambiguous timeout after acceptance can
still duplicate a retry. Persistent DagSentry delivery state prevents ordinary repeated sends.

The Provider accepts any `2xx` response. It retries `408`, `429`, `5xx`, timeouts, and network
failures with bounded attempts. For `429`, it follows a numeric `Retry-After` header with a
60-second cap; otherwise it uses the configured fallback delay. Authentication, authorization,
invalid request, and transient failures map to the same neutral categories used by every
Notification Provider.

## Verification

The Teams adapter passes the shared contract and Teams-specific tests for the Workflows envelope,
non-interactive cards, rate-limit delays, secret URL masking, all three payload types, and runtime
selection:

```shell
uv run pytest tests/test_teams_provider.py
```

Microsoft references:

- [Create incoming Webhooks with Workflows](https://learn.microsoft.com/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook)
- [Teams connector and Adaptive Card request format](https://learn.microsoft.com/connectors/teams/)
- [Office 365 Connector retirement](https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/)
