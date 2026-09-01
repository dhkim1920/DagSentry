# Notification Provider contract

DagSentry's reusable contract suite verifies the behavior shared by HTTP Notification Providers
without adding vendor branches to Diagnosis or Incident Core. The Webhook, Slack, Teams, and
Discord adapters are conforming implementations.

## Required behavior

Every implementation must:

- accept the provider-neutral `NotificationPayload` and a stable `delivery_key`;
- expose a non-empty stable `name` and return the successful HTTP status;
- retain every required neutral payload field in its vendor representation;
- retain the delivery key in a receiver-visible trace or idempotency field;
- retry `408`, `429`, `5xx`, timeouts, and network failures with bounded attempts;
- map failures to `NotificationErrorCategory` and the correct `retryable` decision;
- avoid copying response bodies, transport exception details, or credentials into errors.

Vendor layout, card/block structure, endpoint paths, and authentication headers are intentionally
outside the shared contract. Each adapter verifies those details in its concrete
`assert_success_request` implementation and provider-specific tests.

## Adding an implementation

Create a test class derived from `tests.contracts.notification.NotificationProviderContract`.
Implement two hooks:

- `make_provider`: construct the adapter with two attempts, the supplied mock HTTP handler, and the
  supplied sleep function;
- `assert_success_request`: decode the vendor request and prove that the neutral payload and
  delivery key were retained.

Pytest inherits the full contract automatically:

```shell
uv run pytest tests/test_webhook_provider.py
```

Provider-specific configuration validation and credential-masking tests remain beside the concrete
adapter tests.
