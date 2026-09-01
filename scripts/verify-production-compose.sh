#!/usr/bin/env bash
set -euo pipefail

smoke_root=$(mktemp -d /tmp/dagsentry-production-smoke.XXXXXX)
compose_project=dagsentry-production-smoke
smoke_port=${DAGSENTRY_SMOKE_PORT:-18080}

cleanup() {
    docker compose \
        --project-name "$compose_project" \
        --env-file "$smoke_root/production.env" \
        -f compose.production.yaml \
        down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf -- "$smoke_root"
}
trap cleanup EXIT

cp deployment/production.env.example "$smoke_root/production.env"
printf '%s\n' 'smoke-postgres-password' >"$smoke_root/postgres_password"
printf '%s\n' \
    'postgresql+psycopg://dagsentry:smoke-postgres-password@postgres:5432/dagsentry' \
    >"$smoke_root/database_url"
cp "$smoke_root/database_url" "$smoke_root/migration_database_url"
printf '%s\n' 'smoke-ingest-token' >"$smoke_root/ingest_api_token"
openssl rand -base64 32 >"$smoke_root/connection_encryption_key"
chmod 0600 "$smoke_root"/*

export DAGSENTRY_ENV_FILE="$smoke_root/production.env"
export DAGSENTRY_POSTGRES_PASSWORD_FILE="$smoke_root/postgres_password"
export DAGSENTRY_DATABASE_URL_FILE="$smoke_root/database_url"
export DAGSENTRY_MIGRATION_DATABASE_URL_FILE="$smoke_root/migration_database_url"
export DAGSENTRY_INGEST_API_TOKEN_FILE="$smoke_root/ingest_api_token"
export DAGSENTRY_CONNECTION_ENCRYPTION_KEY_FILE="$smoke_root/connection_encryption_key"
export DAGSENTRY_HTTP_PORT="$smoke_port"
export DAGSENTRY_IMAGE=dagsentry:production-smoke

compose=(
    docker compose
    --project-name "$compose_project"
    --env-file "$smoke_root/production.env"
    -f compose.production.yaml
)

"${compose[@]}" config --quiet
"${compose[@]}" build
"${compose[@]}" up -d --wait api worker scheduler
curl --fail --silent --show-error "http://127.0.0.1:$smoke_port/health/ready" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:$smoke_port/ui/" \
    | grep --quiet '<title>DagSentry'

api_container=$("${compose[@]}" ps -q api)
migrate_container=$("${compose[@]}" ps --all -q migrate)
runtime_policy=$(
    docker inspect --format '{{.Config.User}} {{.HostConfig.ReadonlyRootfs}}' "$api_container"
)
if [ "$runtime_policy" != "10001:10001 true" ]; then
    echo "error: API container is not using the required non-root read-only policy" >&2
    exit 1
fi
if docker inspect --format '{{json .Config.Env}}' "$api_container" \
    | grep --quiet --extended-regexp 'smoke-postgres-password|smoke-ingest-token'; then
    echo "error: Docker Secret value leaked into the stored container environment" >&2
    exit 1
fi
migration_database_mount=$(
    docker inspect --format \
        '{{range .Mounts}}{{if eq .Destination "/run/secrets/DAGSENTRY_DATABASE_URL"}}{{.Source}}{{end}}{{end}}' \
        "$migrate_container"
)
runtime_database_mount=$(
    docker inspect --format \
        '{{range .Mounts}}{{if eq .Destination "/run/secrets/DAGSENTRY_DATABASE_URL"}}{{.Source}}{{end}}{{end}}' \
        "$api_container"
)
if [ -z "$migration_database_mount" ] || [ -z "$runtime_database_mount" ] \
    || [ "$migration_database_mount" = "$runtime_database_mount" ]; then
    echo "error: Migration and runtime database Secret mounts are not separated" >&2
    exit 1
fi

"${compose[@]}" ps --status running api worker scheduler postgres
