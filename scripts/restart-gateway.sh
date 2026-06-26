#!/usr/bin/env bash
# Detached gateway restart: kills the running gateway and starts a fresh one so
# updated module constants (e.g. SESSION_*_BYTES) take effect. Designed to run in
# its own session (setsid) so it survives the death of the PTY shell that launched
# it — the old gateway's shells, including the launcher, get SIGHUP on restart.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG="$ROOT_DIR/scripts/gateway-restart.log"
# Default to the Docker bridge address used by the Caddy reverse proxy in this
# deployment. Override BIND_HOST when running without that proxy.
BIND_HOST="${BIND_HOST:-172.18.0.1}"
PORT="${PORT:-8765}"
SHELL_CMD="${SHELL_CMD:-/bin/bash}"
TOKEN_FILE="${TOKEN_FILE:-$ROOT_DIR/.server-console-token}"

log() { printf '%s  %s\n' "$(date -Is)" "$*" >>"$LOG"; }

: >"$LOG"
log "restart requested (host=$BIND_HOST port=$PORT shell=$SHELL_CMD token_file=$TOKEN_FILE)"

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet server-console.service; then
  log "server-console.service is active, restarting via systemctl"
  if systemctl restart server-console.service >>"$LOG" 2>&1; then
    for _ in $(seq 1 50); do
      if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$PORT" 2>/dev/null | grep -q "$BIND_HOST:$PORT"; then
        log "OK: gateway is listening on $BIND_HOST:$PORT"
        pgrep -af 'server_console_gateway.py' >>"$LOG" 2>&1 || true
        exit 0
      fi
      sleep 0.2
    done
    log "ERROR: service restarted but $BIND_HOST:$PORT is not listening"
    exit 1
  fi
  log "systemctl restart failed, falling back to direct restart"
fi

# Find the running gateway by cmdline, excluding this script.
OLD_PIDS="$(pgrep -f 'server_console_gateway.py' || true)"
log "old gateway pids: ${OLD_PIDS:-<none>}"

for pid in $OLD_PIDS; do
  log "sending SIGTERM to $pid"
  kill "$pid" 2>>"$LOG" || true
done

# Wait up to 10s for graceful exit, then SIGKILL stragglers.
for _ in $(seq 1 50); do
  pgrep -f 'server_console_gateway.py' >/dev/null || break
  sleep 0.2
done
for pid in $(pgrep -f 'server_console_gateway.py' || true); do
  log "force killing $pid"
  kill -9 "$pid" 2>>"$LOG" || true
done

# Wait for the port to be released before rebinding.
for _ in $(seq 1 50); do
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :$PORT" 2>/dev/null | grep -q ":$PORT" || break
  else
    sleep 0.2; break
  fi
  sleep 0.2
done
log "port $PORT released, starting new gateway"

# Start the new gateway fully detached, logging to its own file.
GATEWAY_LOG="$ROOT_DIR/scripts/gateway.log"
BIND_HOST="$BIND_HOST" PORT="$PORT" SHELL_CMD="$SHELL_CMD" TOKEN_FILE="$TOKEN_FILE" \
  setsid nohup bash "$ROOT_DIR/scripts/start-server.sh" >>"$GATEWAY_LOG" 2>&1 &
NEW_PID=$!
log "launched start-server.sh (launcher pid=$NEW_PID), gateway log -> $GATEWAY_LOG"

# Confirm something is listening again.
for _ in $(seq 1 50); do
  if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$PORT" 2>/dev/null | grep -q ":$PORT"; then
    log "OK: gateway is listening on $BIND_HOST:$PORT"
    pgrep -af 'server_console_gateway.py' >>"$LOG" 2>&1 || true
    exit 0
  fi
  sleep 0.2
done

log "ERROR: nothing listening on $PORT after restart; check $GATEWAY_LOG"
exit 1
