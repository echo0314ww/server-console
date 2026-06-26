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

REQUIRE_TOKEN="${REQUIRE_TOKEN:-1}"
case "$REQUIRE_TOKEN" in
  1|true|TRUE|yes|YES)
    REQUIRE_TOKEN="1"
    ;;
  0|false|FALSE|no|NO)
    REQUIRE_TOKEN="0"
    ;;
  *)
    echo "REQUIRE_TOKEN must be 1 or 0." >&2
    exit 1
    ;;
esac

if [[ "$REQUIRE_TOKEN" == "1" ]]; then
  if [[ -n "${TOKEN_FILE:-}" ]]; then
    if [[ ! -r "$TOKEN_FILE" ]]; then
      echo "TOKEN_FILE is set but not readable: $TOKEN_FILE" >&2
      exit 1
    fi
    TOKEN_ARGS=(--token-file "$TOKEN_FILE" --require-token)
  elif [[ -n "${TOKEN:-}" ]]; then
    TOKEN_ARGS=(--token "$TOKEN" --require-token)
  else
    echo "Token authentication is required by default. Set TOKEN_FILE or TOKEN, or set REQUIRE_TOKEN=0 for trusted local development only." >&2
    exit 1
  fi
else
  echo "WARNING: starting PTY gateway without token authentication. Use only on trusted local networks." >&2
fi

python3 gateway/server_console_gateway.py \
  --host "$BIND_HOST" \
  --port "$PORT" \
  --shell "$SHELL_CMD" \
  --mode pty \
  "${TOKEN_ARGS[@]}"
