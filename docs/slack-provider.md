# Slack Notification Provider

DagSentry sends Slack notifications through the Web API `chat.postMessage` method. The same adapter
handles Diagnosis, Incident recovery, and daily report payloads, while Core continues to depend only
on its provider-neutral contracts.

## Slack app setup

Create a Slack app with a bot token and grant the bot the `chat:write` scope. Invite the bot to the
target channel, then configure:

```text
DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=environment
DAGSENTRY_NOTIFICATION_PROVIDER=slack
DAGSENTRY_SLACK_BOT_TOKEN=xoxb-...
DAGSENTRY_SLACK_CHANNEL=C0123456789
```

Alternatively, create an enabled Slack Managed Connection for the target environment and select:

```text
DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=database
DAGSENTRY_CONNECTION_ENCRYPTION_KEY=replace-with-the-generated-value
DAGSENTRY_CONNECTION_ENCRYPTION_KEY_VERSION=1
```

Database mode reads the encrypted bot token plus the channel, API URL, timeout, and retry settings
without falling back to Slack environment values. The Diagnosis Worker resolves one snapshot per
job using the Failure Event environment. Recovery Checker and Daily Report resolve one snapshot for
their configured environment and run. Connection changes therefore apply to the next job or run
without a process restart. A missing, disabled, invalid, or undecryptable connection fails closed.
Database-backed Notification selection currently supports Slack only.

Slack recommends channel-like IDs rather than deprecated channel names. Posting to public channels
without inviting the bot additionally requires `chat:write.public`. DagSentry sends the token only
in the HTTP `Authorization: Bearer` header and masks it from configuration representations.

## Message and delivery behavior

Each request contains accessible top-level fallback text, plain-text Block Kit sections, and message
metadata. The metadata records the DagSentry notification type, resource identifiers, and stable
delivery key. Link unfurling is disabled. Long fields are truncated at Slack's documented text and
Block Kit boundaries; the full source payload remains stored in DagSentry's delivery record.

The Provider checks both HTTP status and Slack's JSON `ok` field because Slack commonly returns API
errors with HTTP 200. It maps authentication, authorization, rate-limit, unavailable, and invalid
request failures to DagSentry's neutral error categories. `408`, `429`, `5xx`, timeouts, network
failures, and Slack transient errors receive bounded retries. A valid `Retry-After` value on HTTP
429 is honored with a 60-second cap.

Slack message metadata makes the delivery key receiver-visible for tracing, but Slack does not
document metadata itself as a strong idempotency guarantee. DagSentry prevents repeat sends through
its persistent delivery state; an ambiguous timeout after Slack accepted a message can still result
in a duplicate on retry.

## Verification

The Slack adapter passes the shared Notification Provider contract plus Slack-specific tests for
HTTP-200 API errors, `Retry-After`, credential masking, all three payload types, and runtime Provider
selection:

```shell
uv run pytest tests/test_slack_provider.py tests/test_runtime_connections.py
```

Slack API references:

- [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)
- [Message metadata](https://docs.slack.dev/messaging/message-metadata/)
- [Web API rate limits](https://docs.slack.dev/apis/web-api/rate-limits/)
