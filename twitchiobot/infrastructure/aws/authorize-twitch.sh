#!/bin/bash
# Friendly entry point for the guided authorization-code setup.

set -euo pipefail
export AWS_PAGER=""

load_env_file() {
    local env_file=".env"
    [ -f "$env_file" ] || return
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        line="${line#export }"
        [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
        local key="${line%%=*}"
        local value="${line#*=}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        [ -z "${!key+x}" ] || continue
        export "$key=$value"
    done < "$env_file"
}

load_env_file

AWS_REGION=${AWS_REGION:-us-east-1}
TWITCH_CREDENTIALS_SECRET_ID=${TWITCH_CREDENTIALS_SECRET_ID:-vieweratlas/twitch/credentials}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/../../.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
fi

exec "$PYTHON_BIN" "$PROJECT_DIR/twitchiobot/scripts/setup_twitch_auth.py" \
    --region "$AWS_REGION" \
    --secret-id "$TWITCH_CREDENTIALS_SECRET_ID" \
    "$@"
