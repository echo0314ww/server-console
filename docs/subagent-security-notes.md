# Subagent Security Notes

Concise notes for the iOS plus server terminal gateway prototype.

## Mobile Remote Terminal Security Checklist

- Use `wss://` only, with strict TLS validation and a narrow allowed origin/subprotocol policy.
- Require short-lived, revocable session tokens; store refresh material only in iOS Keychain.
- Bind sessions to user, device, app build, target host, and least-privilege server identity.
- Run terminal processes under an unprivileged OS account, ideally inside a container or jail with CPU, memory, time, and filesystem limits.
- Separate gateway authorization from PTY execution; every reconnect, resize, input, and signal should still map to an active authorized session.
- Redact secrets from logs and telemetry; never record raw terminal streams unless a user/admin explicitly enables audited session recording.
- Rate-limit auth, session creation, input volume, reconnect loops, and paste-size bursts.
- Provide immediate server-side revocation and client-side session wipe on logout, device loss, or token compromise.
- Audit session lifecycle events: create, attach, detach, target, user, device, IP, duration, exit status, and abnormal closes.
- Treat clipboard paste as risky input: warn or gate very large pastes and multiline commands when policy requires.

## WebSocket Protocol Suggestions

- Version the protocol from the first frame: `hello {version, client, capabilities}` followed by `auth` or token-bound upgrade.
- Keep message types explicit: `input`, `output`, `resize`, `signal`, `ack`, `ping`, `error`, `close`, and `resume`.
- Include `sessionId`, monotonic `seq`, and optional `ack` fields so reconnects can detect gaps and duplicates.
- Let the server PTY be authoritative; the client should render server output, not assume local echo is accepted.
- Use binary frames for raw terminal bytes or JSON envelopes with base64 payloads; avoid ad hoc mixed string formats.
- Coalesce high-frequency resize events and debounce mobile rotation changes.
- Implement flow control: bounded send queues, output backpressure, max frame size, and clear overflow behavior.
- Prefer heartbeat pings plus idle timeouts; distinguish network loss from server-side command exit.
- Consider disabling compression for terminal streams that may contain secrets, or enable it only after risk review.
- Resume with a short-lived resume token; do not buffer sensitive output indefinitely for offline mobile clients.

## UX Pitfalls With Live Output And Local Editing

- Incoming output can visually interrupt a command the user is editing; keep the editable input line stable while appending remote output above it.
- If using a true terminal emulator, avoid separate native text fields that drift from PTY cursor state.
- Do not auto-scroll when the user has intentionally scrolled back; show a subtle new-output affordance instead.
- Preserve composing text from iOS keyboards, IMEs, autocorrect, and hardware keyboards before sending bytes to the PTY.
- Make paste, interrupt, escape, tab, arrow keys, and control chords reachable without crowding the terminal.
- Handle latency honestly: show connection state and queued input state without pretending a command has executed.
- Avoid duplicate echo during lag; if local echo is enabled for responsiveness, reconcile it carefully with server output.
- Protect partially edited input during reconnect, rotation, background/foreground transitions, and keyboard show/hide changes.
- Keep ANSI colors readable in light/dark mode, and test long lines, progress bars, full-screen TUIs, and rapid log streams.
