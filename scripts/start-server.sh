#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BIND_HOST="${BIND_HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
SHELL_CMD="${SHELL_CMD:-${SHELL:-/bin/bash}}"

case "$SHELL_CMD" in
  "/bin/bash --noprofile --norc"|"bash --noprofile --norc")
    SHELL_CMD="/bin/bash"
    ;;
esac

python3 gateway/server_console_gateway.py \
  --host "$BIND_HOST" \
  --port "$PORT" \
  --shell "$SHELL_CMD" \
  --mode pty
