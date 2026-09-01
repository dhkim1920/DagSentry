# SMTP Email Notification Provider

DagSentry can send Diagnosis, Incident recovery, and daily report notifications as plain-text email
through an SMTP server.

## Setup

Configure SMTP with environment-backed Notification settings:

```text
DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=environment
DAGSENTRY_NOTIFICATION_PROVIDER=smtp
DAGSENTRY_SMTP_HOST=smtp.example.com
DAGSENTRY_SMTP_PORT=587
DAGSENTRY_SMTP_USERNAME=alerts@example.com
DAGSENTRY_SMTP_PASSWORD=replace-with-an-app-password
DAGSENTRY_SMTP_FROM=alerts@example.com
DAGSENTRY_SMTP_TO=["oncall@example.com","platform@example.com"]
DAGSENTRY_SMTP_USE_STARTTLS=true
```

`DAGSENTRY_SMTP_TO` is a JSON array. Authentication is optional, but username and password must be
configured together. STARTTLS is enabled by default. For implicit TLS (normally port 465), set
`DAGSENTRY_SMTP_USE_SSL=true` and `DAGSENTRY_SMTP_USE_STARTTLS=false`.

Additional bounded-delivery settings are `DAGSENTRY_SMTP_TIMEOUT_SECONDS`,
`DAGSENTRY_SMTP_MAX_ATTEMPTS`, and `DAGSENTRY_SMTP_RETRY_BACKOFF_SECONDS`.

## Delivery behavior

Each email carries a deterministic subject and plain-text body with the relevant failure, recovery,
or daily-report data. The stable delivery key is included in the `X-DagSentry-Delivery-Key` header
and body for tracing. SMTP does not offer a portable idempotency mechanism, so an ambiguous network
failure after server acceptance can result in a duplicate retry; DagSentry's persisted delivery
state prevents ordinary repeat sends.

SMTP is currently supported only through environment configuration, not Managed Connections.
