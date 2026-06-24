#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BIND_HOST="${BIND_HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
SHELL_CMD="${SHELL_CMD:-${SHELL:-/bin/bash}}"
TOKEN_ARGS=()

case "$SHELL_CMD" in
  "/bin/bash --noprofile --norc"|"bash --noprofile --norc")
    SHELL_CMD="/bin/bash"
    ;;
esac

REQUIRE_TOKEN="${REQUIRE_TOKEN:-0}"

if [[ "$REQUIRE_TOKEN" == "1" && -n "${TOKEN_FILE:-}" ]]; then
  if [[ ! -r "$TOKEN_FILE" ]]; then
    echo "TOKEN_FILE is set but not readable: $TOKEN_FILE" >&2
    exit 1
  fi
  TOKEN_ARGS=(--token-file "$TOKEN_FILE" --require-token)
elif [[ "$REQUIRE_TOKEN" == "1" && -n "${TOKEN:-}" ]]; then
  TOKEN_ARGS=(--token "$TOKEN" --require-token)
fi

python3 gateway/server_console_gateway.py \
  --host "$BIND_HOST" \
  --port "$PORT" \
  --shell "$SHELL_CMD" \
  --mode pty \
  "${TOKEN_ARGS[@]}"
