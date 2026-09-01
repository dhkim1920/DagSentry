#!/bin/sh
set -eu

secret_directory=${DAGSENTRY_SECRETS_DIR:-/run/secrets}

for secret_path in "$secret_directory"/DAGSENTRY_*; do
    if [ ! -f "$secret_path" ]; then
        continue
    fi
    secret_name=${secret_path##*/}
    secret_value=$(cat "$secret_path")
    if [ -z "$secret_value" ]; then
        echo "error: Docker Secret $secret_name is empty" >&2
        exit 78
    fi
    export "$secret_name=$secret_value"
    unset secret_value
done

exec "$@"
