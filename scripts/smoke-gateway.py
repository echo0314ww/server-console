#!/usr/bin/env python3
"""Smoke tests for the dependency-free Server Console gateway."""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
GATEWAY = ROOT_DIR / "gateway" / "server_console_gateway.py"


def main() -> int:
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(GATEWAY),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--mode",
            "demo",
        ],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        wait_for_http(port, process)
        test_static_assets(port)
        test_websocket_handshake(port)
        test_incremental_pty_replay()
        test_incremental_pty_replay_cap()
        test_large_pty_replay_buffer()
        print(f"OK smoke gateway on 127.0.0.1:{port}")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 5
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"gateway exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            http_get(port, "/")
            return
        except Exception as exc:  # noqa: BLE001 - test startup polling.
            last_error = str(exc)
            time.sleep(0.05)
    raise TimeoutError(f"gateway did not start: {last_error}")


def test_static_assets(port: int) -> None:
    index = http_get(port, "/")
    assert_status(index, 200)
    assert_header(index, "Cache-Control", "no-cache")
    if b'<script src="/app.js?v=' not in index.body:
        raise AssertionError("index.html does not reference versioned app.js")

    service_worker = http_get(port, "/service-worker.js")
    assert_status(service_worker, 200)
    assert_header(service_worker, "Cache-Control", "no-cache")

    app_js = http_get(port, "/app.js?v=smoke")
    assert_status(app_js, 200)
    cache_control = app_js.headers.get("Cache-Control", "")
    if cache_control != "no-cache":
        raise AssertionError(f"large app.js should not be cacheable, got {cache_control!r}")

    manifest = http_get(port, "/manifest.webmanifest?v=smoke")
    assert_status(manifest, 200)
    cache_control = manifest.headers.get("Cache-Control", "")
    if "immutable" not in cache_control:
        raise AssertionError(f"small manifest should be immutable, got {cache_control!r}")

    service_worker_body = service_worker.body.decode("utf-8", errors="replace")
    if "CACHE_MAX_BYTES = 32 * 1024" not in service_worker_body:
        raise AssertionError("service worker cache cap is not 32KB")


def test_websocket_handshake(port: int) -> None:
    status, _headers, _body = websocket_handshake(port, origin="")
    if status != 403:
        raise AssertionError(f"missing origin should be 403, got {status}")

    status, headers, body = websocket_handshake(port, origin=f"http://127.0.0.1:{port}", keep_open=True)
    if status != 101:
        raise AssertionError(f"valid websocket should be 101, got {status}")
    if headers.get("sec-websocket-protocol", "").lower() != "server-console":
        raise AssertionError("gateway did not select the server-console subprotocol")
    if b'"type":"hello"' not in body:
        raise AssertionError("valid websocket did not receive hello frame")


def test_incremental_pty_replay() -> None:
    env = dict(os.environ)
    env["SHELL_CMD"] = f"{sys.executable} -q"
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(GATEWAY),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--mode",
            "pty",
            "--shell",
            env["SHELL_CMD"],
        ],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        wait_for_http(port, process)
        status, _headers, frames = websocket_exchange(
            port,
            "/terminal?session=replay-smoke",
            [
                b"print('first')\r",
                b"print('second')\r",
            ],
            expected_binary_frames=2,
        )
        if status != 101:
            raise AssertionError(f"initial replay websocket should be 101, got {status}")
        sequences = [sequence for opcode, sequence, _payload in frames if opcode == 0x2 and sequence > 0]
        if not sequences:
            raise AssertionError("initial pty websocket did not receive sequenced output")

        after = max(sequences)
        hello_payload = next((payload for opcode, _seq, payload in frames if opcode == 0x1), b"")
        hello = json_loads_bytes(hello_payload)
        created_at = hello.get("createdAt")
        if not isinstance(created_at, (int, float)) or created_at <= 0:
            raise AssertionError(f"initial hello did not include createdAt: {hello!r}")
        status, _headers, frames = websocket_exchange(
            port,
            f"/terminal?session=replay-smoke&after={after}&createdAt={created_at}",
            [b"print('third')\r"],
            expected_binary_frames=2,
        )
        if status != 101:
            raise AssertionError(f"resume replay websocket should be 101, got {status}")

        hello_payload = next((payload for opcode, _seq, payload in frames if opcode == 0x1), b"")
        hello = json_loads_bytes(hello_payload)
        if int(hello.get("replayAfter", 0)) != after:
            raise AssertionError(f"resume hello replayAfter mismatch: {hello!r}")
        if hello.get("replayIncremental") is not True:
            raise AssertionError(f"resume hello should mark contiguous replay incremental: {hello!r}")

        old_sequences = [
            sequence
            for opcode, sequence, payload in frames
            if opcode == 0x2 and sequence <= after and payload
        ]
        if len(old_sequences) > 1:
            raise AssertionError(f"resume replay sent too many old chunks: {old_sequences!r}")
        new_sequences = [
            sequence
            for opcode, sequence, payload in frames
            if opcode == 0x2 and sequence > after and payload
        ]
        if not new_sequences:
            raise AssertionError(f"resume replay did not receive new output after {after}: {frames!r}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_large_pty_replay_buffer() -> None:
    env = dict(os.environ)
    env["SHELL_CMD"] = f"{sys.executable} -q"
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(GATEWAY),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--mode",
            "pty",
            "--shell",
            env["SHELL_CMD"],
        ],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        wait_for_http(port, process)
        status, _headers, _frames = websocket_exchange(
            port,
            "/terminal?session=large-replay-smoke",
            [b"import sys; sys.stdout.write('R' * 70000); sys.stdout.flush()\r"],
            expected_binary_frames=1,
            min_binary_payload_bytes=70000,
        )
        if status != 101:
            raise AssertionError(f"large replay initial websocket should be 101, got {status}")

        time.sleep(0.5)
        status, _headers, frames = websocket_exchange(
            port,
            "/terminal?session=large-replay-smoke",
            [],
            expected_binary_frames=1,
            min_binary_payload_bytes=70000,
        )
        if status != 101:
            raise AssertionError(f"large replay resume websocket should be 101, got {status}")
        replayed_r_bytes = sum(payload.count(b"R") for opcode, _seq, payload in frames if opcode == 0x2)
        if replayed_r_bytes < 60000:
            raise AssertionError(f"large replay restored too little output: {replayed_r_bytes} R bytes")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_incremental_pty_replay_cap() -> None:
    env = dict(os.environ)
    env["SHELL_CMD"] = f"{sys.executable} -q"
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(GATEWAY),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--mode",
            "pty",
            "--shell",
            env["SHELL_CMD"],
        ],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        wait_for_http(port, process)
        status, _headers, frames = websocket_exchange(
            port,
            "/terminal?session=replay-cap-smoke",
            [b"print('ready')\r"],
            expected_binary_frames=1,
        )
        if status != 101:
            raise AssertionError(f"replay cap initial websocket should be 101, got {status}")
        hello_payload = next((payload for opcode, _seq, payload in frames if opcode == 0x1), b"")
        hello = json_loads_bytes(hello_payload)
        created_at = hello.get("createdAt")
        if not isinstance(created_at, (int, float)) or created_at <= 0:
            raise AssertionError(f"replay cap initial hello missing createdAt: {hello!r}")
        sequences = [sequence for opcode, sequence, _payload in frames if opcode == 0x2 and sequence > 0]
        if not sequences:
            raise AssertionError("replay cap initial pty websocket did not receive sequenced output")
        after = max(sequences)

        status, _headers, _frames = websocket_exchange(
            port,
            "/terminal?session=replay-cap-smoke",
            [b"import sys; sys.stdout.write('C' * (2 * 1024 * 1024)); sys.stdout.flush()\r"],
            expected_binary_frames=1,
            min_binary_payload_bytes=1024 * 1024,
        )
        if status != 101:
            raise AssertionError(f"replay cap producer websocket should be 101, got {status}")

        status, _headers, frames = websocket_exchange(
            port,
            f"/terminal?session=replay-cap-smoke&after={after}&createdAt={created_at}",
            [],
            expected_binary_frames=1,
        )
        if status != 101:
            raise AssertionError(f"replay cap resume websocket should be 101, got {status}")
        hello_payload = next((payload for opcode, _seq, payload in frames if opcode == 0x1), b"")
        hello = json_loads_bytes(hello_payload)
        replay_bytes = int(hello.get("replayBytes", 0) or 0)
        if replay_bytes > 128 * 1024:
            raise AssertionError(f"incremental replay exceeded 128KiB cap: {hello!r}")
        if hello.get("replayGap") is not True:
            raise AssertionError(f"replay cap resume should report a replay gap: {hello!r}")
        if hello.get("replayIncremental") is not False:
            raise AssertionError(f"replay cap resume should not be marked incremental: {hello!r}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class HttpResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body


def http_get(port: int, path: str) -> HttpResponse:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Connection": "close"})
    try:
        with opener.open(request, timeout=5) as response:
            headers = {key: value for key, value in response.headers.items()}
            return HttpResponse(response.status, headers, response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, {key: value for key, value in exc.headers.items()}, exc.read())


def assert_status(response: HttpResponse, expected: int) -> None:
    if response.status != expected:
        raise AssertionError(f"expected HTTP {expected}, got {response.status}")


def assert_header(response: HttpResponse, key: str, expected: str) -> None:
    actual = response.headers.get(key, "")
    if actual != expected:
        raise AssertionError(f"expected {key}: {expected!r}, got {actual!r}")


def websocket_handshake(
    port: int,
    origin: str,
    keep_open: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    host = f"127.0.0.1:{port}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")

    lines = [
        "GET /terminal?session=smoke HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        "Sec-WebSocket-Protocol: server-console",
    ]
    if origin:
        lines.append(f"Origin: {origin}")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")

    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(request)
        raw_headers, buffered = read_headers(sock)
        status, headers = parse_http_headers(raw_headers)
        body = b""
        if status == 101:
            opcode, payload = read_ws_frame(sock, buffered)
            if opcode == 0x1:
                body = payload
            if keep_open:
                send_close_frame(sock)
        else:
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length:
                body = read_exact(sock, content_length, buffered)
        return status, headers, body


def websocket_exchange(
    port: int,
    path: str,
    input_payloads: list[bytes],
    expected_binary_frames: int,
    min_binary_payload_bytes: int = 0,
) -> tuple[int, dict[str, str], list[tuple[int, int, bytes]]]:
    host = f"127.0.0.1:{port}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "\r\n".join(
            [
                f"GET {path} HTTP/1.1",
                f"Host: {host}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {key}",
                "Sec-WebSocket-Version: 13",
                "Sec-WebSocket-Protocol: server-console",
                f"Origin: http://{host}",
            ]
        )
        + "\r\n\r\n"
    ).encode("ascii")

    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(request)
        raw_headers, buffered = read_headers(sock)
        status, headers = parse_http_headers(raw_headers)
        frames: list[tuple[int, int, bytes]] = []
        if status != 101:
            return status, headers, frames

        opcode, payload, buffered = read_ws_frame_with_buffer(sock, buffered)
        frames.append(decode_server_frame(opcode, payload))
        for payload in input_payloads:
            send_binary_input_frame(sock, payload)

        deadline = time.time() + 5
        while (
            (
                count_binary_frames(frames) < expected_binary_frames
                or binary_payload_bytes(frames) < min_binary_payload_bytes
            )
            and time.time() < deadline
        ):
            try:
                opcode, payload, buffered = read_ws_frame_with_buffer(sock, buffered)
            except socket.timeout:
                continue
            frames.append(decode_server_frame(opcode, payload))

        send_close_frame(sock)
        return status, headers, frames


def count_binary_frames(frames: list[tuple[int, int, bytes]]) -> int:
    return sum(1 for opcode, _sequence, payload in frames if opcode == 0x2 and payload)


def binary_payload_bytes(frames: list[tuple[int, int, bytes]]) -> int:
    return sum(len(payload) for opcode, _sequence, payload in frames if opcode == 0x2 and payload)


def decode_server_frame(opcode: int, payload: bytes) -> tuple[int, int, bytes]:
    if opcode == 0x2 and len(payload) >= 5 and payload[0] == 0:
        return opcode, struct.unpack("!I", payload[1:5])[0], payload[5:]
    return opcode, 0, payload


def send_binary_input_frame(sock: socket.socket, payload: bytes) -> None:
    send_masked_ws_frame(sock, b"\x00" + payload, opcode=0x2)


def send_masked_ws_frame(sock: socket.socket, payload: bytes, opcode: int) -> None:
    mask = os.urandom(4)
    header = encode_client_frame_header(opcode, len(payload))
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(header + mask + masked)


def encode_client_frame_header(opcode: int, length: int) -> bytes:
    first = 0x80 | opcode
    if length < 126:
        return struct.pack("!BB", first, 0x80 | length)
    if length <= 0xFFFF:
        return struct.pack("!BBH", first, 0x80 | 126, length)
    return struct.pack("!BBQ", first, 0x80 | 127, length)


def json_loads_bytes(payload: bytes) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    marker = b"\r\n\r\n"
    data = b""
    while marker not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, separator, tail = data.partition(marker)
    if not separator:
        raise EOFError("socket closed before HTTP headers completed")
    return head + separator, tail


def read_exact(sock: socket.socket, length: int, buffered: bytes = b"") -> bytes:
    data = buffered[:length]
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise EOFError("socket closed before expected bytes arrived")
        data += chunk
    return data


def parse_http_headers(raw: bytes) -> tuple[int, dict[str, str]]:
    header_text = raw.decode("iso-8859-1")
    lines = header_text.split("\r\n")
    status = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return status, headers


def read_ws_frame(sock: socket.socket, buffered: bytes = b"") -> tuple[int, bytes]:
    opcode, payload, _buffered = read_ws_frame_with_buffer(sock, buffered)
    return opcode, payload


def read_ws_frame_with_buffer(sock: socket.socket, buffered: bytes = b"") -> tuple[int, bytes, bytes]:
    header = read_exact(sock, 2, buffered)
    buffered = buffered[2:]
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        extended = read_exact(sock, 2, buffered)
        buffered = buffered[2:]
        length = struct.unpack("!H", extended)[0]
    elif length == 127:
        extended = read_exact(sock, 8, buffered)
        buffered = buffered[8:]
        length = struct.unpack("!Q", extended)[0]
    payload = read_exact(sock, length, buffered)
    buffered = buffered[length:]
    return opcode, payload, buffered


def send_close_frame(sock: socket.socket) -> None:
    send_masked_ws_frame(sock, b"", opcode=0x8)


if __name__ == "__main__":
    raise SystemExit(main())
