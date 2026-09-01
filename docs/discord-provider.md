# Discord Notification Provider

DagSentry uses a Discord Incoming Webhook to deliver Diagnosis, Incident recovery, and daily report
messages. Incoming Webhooks are channel-specific HTTP endpoints and do not require a bot user or a
persistent connection.

## Setup

Create an Incoming Webhook for the target Discord channel, copy its URL, and configure:

```text
DAGSENTRY_NOTIFICATION_PROVIDER=discord
DAGSENTRY_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/id/token
```

The URL contains a secret token. DagSentry does not add it to logs or errors and masks the complete
URL in configuration representations.

## Message and delivery behavior

The Provider sends accessible fallback `content` plus one rich embed. Diagnosis messages include
the failure identity, Incident state, classification, Evidence, recommended actions, signature, and
Airflow log URL. Recovery and daily report payloads use the same adapter. Every request disables
all automatic mentions with `allowed_mentions.parse=[]`, so DAG names, errors, and AI prose cannot
unexpectedly ping Discord users or roles.

DagSentry sets `wait=true`, which asks Discord to wait for the message to be saved and return the
created message instead of accepting a fire-and-forget request. The stable delivery key is included
in the embed footer for receiver-side tracing. Discord does not document Webhook execution as a
strong idempotency API, so an ambiguous timeout after acceptance can still duplicate a retry; the
persistent DagSentry delivery state prevents ordinary repeated sends.

The Provider retries `408`, `429`, `5xx`, timeouts, and network failures with bounded attempts. On
HTTP 429 it follows Discord's `Retry-After` header first, then the JSON `retry_after` field, with a
60-second cap. Authentication, authorization, invalid request, and transient failures map to the
same neutral categories used by every Notification Provider.

## Verification

The Discord adapter passes the shared contract and Discord-specific tests for mention safety,
rate-limit delays, secret URL masking, all three payload types, and runtime selection:

```shell
uv run pytest tests/test_discord_provider.py
```

Discord API references:

- [Webhook resource](https://docs.discord.com/developers/resources/webhook)
- [Rate limits](https://docs.discord.com/developers/topics/rate-limits)
