# Managed Connections

Managed Connections are DagSentry-owned outbound settings for Airflow, LLM, and Notification
Providers. They are not Airflow Connection objects. Each environment has at most one connection for
each purpose.

## Encryption key

Provider Secrets are encrypted with AES-256-GCM before they are stored. Configure a Base64 encoding
of exactly 32 random bytes and a positive key version:

```shell
openssl rand -base64 32
```

```dotenv
DAGSENTRY_CONNECTION_ENCRYPTION_KEY=replace-with-the-generated-value
DAGSENTRY_CONNECTION_ENCRYPTION_KEY_VERSION=1
```

The key must be supplied through the deployment Secret mechanism and must not be committed to Git,
stored in the database, or included in an image. `.env` and `.env.*` are ignored; `.env.example`
contains placeholders only. If the key is absent, malformed, or does not match the stored key
version, Secret writes and decryption fail closed. Non-secret Ollama metadata can still be saved.

The connection ID, environment, purpose, and Provider are authenticated as AES-GCM associated data.
Changing any of them on a connection that already has a Secret therefore requires an explicit
Secret replacement.

## Admin API

All endpoints require an Admin session:

- `GET /api/v1/admin/connections`
- `GET /api/v1/admin/connections/{connection_id}`
- `PUT /api/v1/admin/connections/{connection_id}`
- `POST /api/v1/admin/connections/{connection_id}/disable`
- `POST /api/v1/admin/connections/{connection_id}/test`

`PUT` creates a caller-selected UUID when `expected_version` is omitted. Updating an existing record
requires its current positive `expected_version`; stale writes return `409 Conflict`. Omitting or
setting `secret` to `null` preserves the existing ciphertext. Providing `secret` explicitly replaces
it with a newly encrypted value and fresh nonce.

Responses expose `secret_configured: true|false` only. They never return the Secret, ciphertext,
nonce, or encryption key version. Admin audit summaries record changed field names and connection
versions without config Secret values.

Provider-specific non-secret and Secret objects reject unknown fields. Webhook, Teams, and Discord
URLs are Secret fields because their URLs commonly contain authentication signatures or tokens.
Runtime processes can use enabled environment-specific connections by setting the corresponding
`DAGSENTRY_AIRFLOW_CONFIG_SOURCE`, `DAGSENTRY_LLM_CONFIG_SOURCE`, or
`DAGSENTRY_NOTIFICATION_CONFIG_SOURCE` to `database`. Airflow, Ollama, and Slack load immutable
snapshots for each applicable job or one-shot run and fail closed without falling back to
environment values. Database-backed selection currently supports Ollama for LLM and Slack for
Notification; the remaining Providers are separate delivery steps.

## Admin UI and read-only tests

The Admin destination lists every Managed Connection and supports creation and editing for Airflow,
Ollama, and Slack. Secret fields are write-only password inputs. Existing values are represented
only as `•••••• Configured`; leaving the input blank during an edit preserves the existing encrypted
value. The UI never receives ciphertext, a nonce, a key version, or plaintext Secret material.

An Admin can explicitly run these read-only checks for an enabled connection:

- Airflow: `GET {api_base_url}/version`;
- Ollama: `GET {api_base_url}/tags`;
- Slack: `auth.test`, followed by `conversations.info` for the configured channel.

Tests do not follow redirects or retry, and Slack testing never sends a message. OpenAI, Azure
OpenAI, Anthropic, and Bedrock tests are not exposed because a useful request may be billable.
Webhook, Teams, and Discord tests are not exposed because their available checks would send a
notification. Provider response bodies and exception messages are discarded. Only `PASSED` or
`FAILED`, an allowlisted error category, and the UTC test time are returned, persisted, and audited.
If the connection changes while its external check is running, the stale result is rejected with
`409 Conflict`.

## Encryption key rotation

Use a maintenance window because processes configured with the old key cannot decrypt records after
the transaction commits. Rotation locks Managed Connection Secret writes in PostgreSQL, decrypts
every configured Secret, and re-encrypts all of them with fresh nonces in one transaction. A failed
record rolls back every change.

1. Stop the API and worker processes that can read or write Managed Connections.
2. Take and verify a database backup while retaining the current deployment key securely.
3. Generate a new 32-byte Base64 key and select a never-before-used, strictly larger version.
4. Run the interactive command below. Enter the current key once and the new key twice at the hidden
   prompts; neither key is accepted as a command argument.

   ```shell
   uv run dagsentry-admin connections rotate-key --current-version 1 --new-version 2
   ```

5. Confirm that the JSON result reports the expected `connections_rotated` count. Retain its
   `correlation_id` for matching `connection.secret_rotated` Admin audit events.
6. Replace the deployment Secret with the new key and set
   `DAGSENTRY_CONNECTION_ENCRYPTION_KEY_VERSION=2` before restarting DagSentry.
7. Verify Admin connection reads and read-only connection tests before ending the maintenance
   window.

Never place either key in shell arguments, shell history, logs, tickets, or the database. Key
versions must increase and must not be reused. If post-rotation verification fails, stop DagSentry
and restore the database backup together with the previous key and version; restoring only one side
leaves the vault undecryptable.
