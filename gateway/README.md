# Server Console Gateway

This gateway serves the browser PWA and exposes one interactive shell over WebSocket. It uses only the Python standard library and a real PTY on Unix-like systems.

## Start

Windows preview:

```powershell
.\scripts\start-preview.ps1
```

Linux server with real PTY:

```bash
BIND_HOST=127.0.0.1 ./scripts/start-server.sh
```

Optional shell command:

```bash
SHELL_CMD=/usr/bin/zsh ./scripts/start-server.sh
SHELL_CMD="tmux new-session -A -s phone" ./scripts/start-server.sh
```

## Connect

Open the PWA:

```text
http://SERVER_IP:8765/
```

The PWA uses this WebSocket endpoint:

```text
ws://SERVER_IP:8765/terminal?session=phone
```

The app sends local input only when the user taps send. Terminal output continues to stream while local input is being edited.
The top bar also includes a `New` button that asks the gateway to start a fresh terminal for the same session id.

## Production Hardening

- Bind the gateway to `127.0.0.1`.
- Put Nginx, Caddy, or another reverse proxy in front with TLS.
- Use `wss://`, not plain `ws://`, outside trusted local testing.
- Run as a restricted user.
- Prefer a locked-down container or VM for risky commands.
- Re-enable `--require-token` if you need access control again.
