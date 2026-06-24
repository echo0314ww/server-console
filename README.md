# Server Console

Server Console is a prototype mobile remote terminal system:

- `web`: iPhone-friendly PWA that runs in Safari and can be added to the home screen.
- `gateway`: dependency-free Python WebSocket gateway that serves the PWA and attaches to a real PTY.
- `ios/ServerConsole`: optional native SwiftUI project. It still requires macOS/Xcode.

The app keeps terminal output and local input editing independent. The server can continue streaming output while the user is still editing text locally, which is the behavior needed for interactive sessions and local `/` commands.

## Layout

```text
.
+-- gateway/
|   +-- README.md
|   +-- server_console_gateway.py
+-- web/
|   +-- index.html
|   +-- app.js
|   +-- styles.css
+-- ios/
    +-- ServerConsole/
        +-- ServerConsole.xcodeproj/
        +-- ServerConsole/
```

## Run The Gateway

### Windows Preview

On your Windows computer, preview the PWA and WebSocket behavior with demo output:

```powershell
.\scripts\start-preview.ps1
```

Then open:

```text
http://127.0.0.1:8765/
```

This preview does not execute real server commands. It proves the mobile UI, WebSocket streaming, local `/` commands, and "output continues while editing" behavior.

### Linux Server

```bash
umask 077
openssl rand -hex 24 > .server-console-token
BIND_HOST=127.0.0.1 TOKEN_FILE=.server-console-token SHELL_CMD="tmux new-session -A -s phone" ./scripts/start-server.sh
```

For local LAN testing you can bind to `0.0.0.0`, but do not expose it to the public internet without TLS and a reverse proxy:

```bash
BIND_HOST=0.0.0.0 TOKEN_FILE=.server-console-token ./scripts/start-server.sh
```

Then open this URL on your iPhone:

```text
http://SERVER_IP:8765/
```

In Safari, use Share -> Add to Home Screen to install it like an app.

Enter the WebSocket URL in the connection panel:

```text
ws://SERVER_IP:8765/terminal?session=phone
```

For production, put the gateway behind HTTPS and use:

```text
wss://your-domain.example/terminal?session=phone
```

## Native iOS App Is Optional

The native project is still here, but building it requires macOS and Xcode:

```text
ios/ServerConsole/ServerConsole.xcodeproj
```

CLI build on macOS:

```bash
scripts/build-ios.sh
```

The script writes Xcode DerivedData to `ios/ServerConsole/DerivedData` inside this project folder.

If you only have Windows, use the PWA path above.

## Protocol

The WebSocket protocol uses JSON text messages.

Client to server:

```json
{"type":"input","data":"base64 bytes"}
{"type":"resize","cols":100,"rows":30}
{"type":"ping"}
{"type":"restart"}
```

When token auth is enabled, the browser sends the token through the
`server-console-token.<base64url-token>` WebSocket subprotocol. Query-string
tokens are still accepted by the gateway for compatibility, but the PWA strips
them from the visible URL before saving settings.

Server to client:

```json
{"type":"hello","shell":"/bin/bash","cols":100,"rows":30,"cwd":"/home/user","createdAt":1234567890.0,"replayChunks":0,"replayBytes":0}
{"type":"cwd","cwd":"/home/user/project"}
{"type":"output","data":"base64 bytes","seq":1}
{"type":"exit","code":0}
{"type":"error","message":"..."}
{"type":"pong","time":1234567890.0}
```

## Security Notes

This is intentionally a prototype, but it controls a server shell and must be treated as high risk.

- Do not run the gateway as root.
- Prefer `127.0.0.1` plus a TLS reverse proxy.
- Add user authentication before sharing this with anyone.
- Add audit logging for commands and connection metadata.
- Consider command allowlists or isolated containers for production.
- Use `tmux` as the shell command when you need reconnectable sessions.
