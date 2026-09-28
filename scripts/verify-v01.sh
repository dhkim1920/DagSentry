#!/usr/bin/env bash
# Compatibility entry point; use verify.sh for current releases.
set -euo pipefail
exec bash "$(dirname "${BASH_SOURCE[0]}")/verify.sh" "$@"
