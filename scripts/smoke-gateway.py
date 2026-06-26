#!/usr/bin/env python3
"""Smoke tests for the dependency-free Server Console gateway."""

from __future__ import annotations

import base64
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
TOKEN = "smoke-test-token-12345"


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
            "--token",
            TOKEN,
            "--require-token",
        ],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        wait_for_http(port, process)
        test_static_assets(port)
        test_websocket_auth(port)
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
    if "immutable" not in cache_control:
        raise AssertionError(f"app.js should be immutable, got {cache_control!r}")


def test_websocket_auth(port: int) -> None:
    status, _headers, _body = websocket_handshake(port, token="", origin=f"http://127.0.0.1:{port}")
    if status != 401:
        raise AssertionError(f"missing token should be 401, got {status}")

    status, _headers, _body = websocket_handshake(port, token="wrong-token", origin=f"http://127.0.0.1:{port}")
    if status != 401:
        raise AssertionError(f"wrong token should be 401, got {status}")

    status, _headers, _body = websocket_handshake(port, token=TOKEN, origin="")
    if status != 403:
        raise AssertionError(f"missing origin should be 403, got {status}")

    status, headers, body = websocket_handshake(port, token=TOKEN, origin=f"http://127.0.0.1:{port}", keep_open=True)
    if status != 101:
        raise AssertionError(f"valid websocket should be 101, got {status}")
    if headers.get("sec-websocket-protocol", "").lower() != "server-console":
        raise AssertionError("gateway did not select the server-console subprotocol")
    if b'"type":"hello"' not in body:
        raise AssertionError("valid websocket did not receive hello frame")


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
    token: str,
    origin: str,
    keep_open: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    host = f"127.0.0.1:{port}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    protocols = ["server-console"]
    if token:
        protocols.append(f"server-console-token.{base64_url_encode(token)}")

    lines = [
        "GET /terminal?session=smoke HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        f"Sec-WebSocket-Protocol: {', '.join(protocols)}",
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
    return opcode, read_exact(sock, length, buffered)


def send_close_frame(sock: socket.socket) -> None:
    mask = os.urandom(4)
    payload = b""
    sock.sendall(struct.pack("!BB", 0x88, 0x80 | len(payload)) + mask)


def base64_url_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


if __name__ == "__main__":
    raise SystemExit(main())
