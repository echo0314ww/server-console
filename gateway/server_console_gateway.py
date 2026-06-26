#!/usr/bin/env python3
"""
Dependency-free WebSocket to PTY gateway for Server Console.

This is a small prototype server intended for Unix-like hosts. It accepts one
WebSocket per shell session and forwards JSON messages containing base64-encoded
terminal bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import gzip
import hashlib
import json
import mimetypes
import os
import pwd
import shlex
import signal
import stat
import struct
import subprocess
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from typing import Optional

try:
    import fcntl
    import pty
    import termios
    PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows preview path.
    fcntl = None
    pty = None
    termios = None
    PTY_AVAILABLE = False


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME_BYTES = 1024 * 1024
SESSION_OUTPUT_BUFFER_BYTES = 32 * 1024
SESSION_REPLAY_BYTES = 32 * 1024
# Must stay <= SESSION_OUTPUT_BUFFER_BYTES so the newest chunk survives
# _trim_output_buffer() and remains available for replay on reconnect.
PTY_READ_CHUNK_BYTES = 32 * 1024
SESSION_IDLE_TIMEOUT_SECONDS = 12 * 60 * 60
CWD_POLL_INTERVAL_SECONDS = 1.0
CWD_POLL_MAX_INTERVAL_SECONDS = 5.0
CWD_POLL_TIMEOUT_SECONDS = 0.5
ATTACHED_WRITER_CLOSE_TIMEOUT_SECONDS = 1.0
WEBSOCKET_DRAIN_TIMEOUT_SECONDS = 5.0
HTTP_REQUEST_READ_TIMEOUT_SECONDS = 10.0
MAX_KEEPALIVE_REQUESTS = 64
PTY_WRITE_TIMEOUT_SECONDS = 10.0
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT_DIR, "web")
WEB_ROOT = os.path.abspath(WEB_DIR)
SESSION_ID_ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
# Binary WebSocket data frames (opcode 0x2) carry terminal bytes without base64,
# avoiding base64's ~33% inflation and the per-chunk encode/decode cost on both
# ends. Control messages (hello/cwd/exit/error/pong) stay JSON text frames.
#   server -> client output: [0x00][uint32 big-endian sequence][raw pty bytes]
#   client -> server input:  [0x00][raw input bytes]
BINARY_TYPE_DATA = 0x00
BINARY_TYPE_DATA_BYTE = b"\x00"
GZIPPABLE_TYPES = (
    "text/",
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
)
STATIC_FILE_CACHE: dict[str, tuple[int, int, bytes, str, Optional[bytes]]] = {}


@dataclass(slots=True)
class GatewayConfig:
    host: str
    port: int
    shell: str
    cols: int
    rows: int
    mode: str


@dataclass(frozen=True, slots=True)
class CwdProbe:
    pid: int
    tmux_command: Optional[tuple[str, ...]]


@dataclass(slots=True)
class OutputChunk:
    sequence: int
    data: bytes
    raw_length: int
    frame: bytes

    def encoded_frame(self) -> bytes:
        if not self.frame:
            self.frame = encode_pty_output_frame(self.data, self.sequence)
            self.data = b""
        return self.frame


@dataclass(slots=True)
class SessionWriter:
    lock: asyncio.Lock
    ready: bool = False


class WebSocketProtocolError(Exception):
    pass


class HttpRequestError(Exception):
    pass


class SessionAttachError(Exception):
    pass


def parse_headers(raw: bytes) -> tuple[str, dict[str, str]]:
    text = raw.decode("iso-8859-1")
    lines = text.split("\r\n")
    request_line = lines[0]
    headers: dict[str, str] = {}

    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    return request_line, headers


def websocket_accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


async def read_http_request(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    raw = await asyncio.wait_for(
        reader.readuntil(b"\r\n\r\n"),
        timeout=HTTP_REQUEST_READ_TIMEOUT_SECONDS,
    )
    return parse_headers(raw)


def websocket_protocol_values(headers: dict[str, str]) -> list[str]:
    return [
        value.strip()
        for value in headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]


def validate_same_origin(headers: dict[str, str]) -> bool:
    origin = headers.get("origin", "")
    if not origin:
        # A browser always sends Origin on a WebSocket upgrade. Treating a
        # missing Origin as same-origin enables cross-site WebSocket hijacking
        # and lets non-browser clients bypass the check entirely. Reject it.
        return False

    host = headers.get("host", "").split(",", 1)[0].strip().lower()
    if not host:
        return False

    parsed = urllib.parse.urlparse(origin)
    return parsed.netloc.lower() == host


def selected_subprotocol(headers: dict[str, str]) -> str:
    protocols = websocket_protocol_values(headers)
    return "server-console" if "server-console" in protocols else ""


def request_query(request_line: str) -> dict[str, list[str]]:
    parts = request_line.split()
    if len(parts) < 2:
        return {}
    parsed = urllib.parse.urlparse(parts[1])
    return urllib.parse.parse_qs(parsed.query)


def session_id_from_request(request_line: str) -> str:
    params = request_query(request_line)
    value = params.get("session", ["phone"])[0].strip() or "phone"
    cleaned = "".join(char for char in value[:64] if char in SESSION_ID_ALLOWED_CHARS)
    return cleaned or "phone"


async def send_http_error(writer: asyncio.StreamWriter, code: int, message: str) -> None:
    body = f"{code} {message}\n".encode("utf-8")
    writer.write(
        f"HTTP/1.1 {code} {message}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n".encode("ascii")
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def send_http_response(
    writer: asyncio.StreamWriter,
    code: int,
    message: str,
    body: bytes,
    content_type: str,
    content_encoding: Optional[str] = None,
    cache_control: str = "no-cache",
    vary_accept_encoding: bool = False,
    keep_alive: bool = False,
) -> None:
    encoding_header = f"Content-Encoding: {content_encoding}\r\n" if content_encoding else ""
    vary_header = "Vary: Accept-Encoding\r\n" if vary_accept_encoding else ""
    connection_header = "Connection: keep-alive\r\n" if keep_alive else "Connection: close\r\n"
    writer.write(
        f"HTTP/1.1 {code} {message}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"{encoding_header}"
        f"{vary_header}"
        f"Cache-Control: {cache_control}\r\n"
        f"{connection_header}"
        "\r\n".encode("ascii")
        + body
    )
    await writer.drain()
    if not keep_alive:
        writer.close()
        await writer.wait_closed()


def request_path(request_line: str) -> str:
    parts = request_line.split()
    if len(parts) < 2 or parts[0].upper() != "GET":
        raise HttpRequestError("Only GET is supported.")
    return urllib.parse.urlparse(parts[1]).path


def request_wants_close(request_line: str, headers: dict[str, str]) -> bool:
    # HTTP/1.0 defaults to close unless it explicitly opts into keep-alive;
    # HTTP/1.1 defaults to keep-alive unless it asks to close.
    connection = headers.get("connection", "").lower()
    if "close" in connection:
        return True
    version = request_line.rsplit(" ", 1)[-1].strip().upper() if request_line else ""
    if version == "HTTP/1.0":
        return "keep-alive" not in connection
    return False


def static_file_for_path(path: str) -> Optional[str]:
    if path == "/":
        path = "/index.html"

    normalized = os.path.normpath(path.lstrip("/"))
    full_path = os.path.abspath(os.path.join(WEB_DIR, normalized))

    if not full_path.startswith(WEB_ROOT + os.sep) and full_path != WEB_ROOT:
        return None
    return full_path


def static_file_exists(full_path: Optional[str]) -> bool:
    if not full_path:
        return False
    try:
        stat_result = os.stat(full_path)
    except OSError:
        return False
    return stat.S_ISREG(stat_result.st_mode)


def accepts_gzip(accept_encoding: str) -> bool:
    wildcard_quality = 0.0
    for value in accept_encoding.split(","):
        parts = [part.strip().lower() for part in value.split(";")]
        if not parts or not parts[0]:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            key, separator, raw_value = parameter.partition("=")
            if key.strip() == "q" and separator:
                try:
                    quality = float(raw_value.strip())
                except ValueError:
                    quality = 0.0
        if parts[0] == "gzip":
            return quality > 0
        if parts[0] == "*":
            wildcard_quality = quality
    return wildcard_quality > 0


def is_gzippable_content_type(content_type: str) -> bool:
    return content_type.startswith(GZIPPABLE_TYPES)


def cache_control_for_path(path: str) -> str:
    if path in ("/", "/index.html", "/service-worker.js"):
        return "no-cache"
    return "public, max-age=31536000, immutable"


def static_content_type(full_path: str) -> str:
    content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    if full_path.endswith(".webmanifest"):
        content_type = "application/manifest+json"
    return content_type


def prewarm_static_cache() -> None:
    # Populate STATIC_FILE_CACHE (and precompute gzip for eligible files) at
    # startup so the first request for a large asset like xterm.min.js (~283KB)
    # does not pay read + level-9 compress latency on the event loop, which would
    # otherwise stall every connection during that window. Best-effort: unreadable
    # files are simply left to the lazy path in serve_static_file().
    for dirpath, _dirnames, filenames in os.walk(WEB_DIR):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            try:
                stat_result = os.stat(full_path)
                if not stat.S_ISREG(stat_result.st_mode):
                    continue
                with open(full_path, "rb") as file:
                    body = file.read()
            except OSError:
                continue
            content_type = static_content_type(full_path)
            gzipped_body = None
            if len(body) >= 1024 and is_gzippable_content_type(content_type):
                gzipped_body = gzip.compress(body)
            STATIC_FILE_CACHE[full_path] = (
                stat_result.st_mtime_ns,
                stat_result.st_size,
                body,
                content_type,
                gzipped_body,
            )


async def serve_static_file(
    writer: asyncio.StreamWriter,
    path: str,
    accept_encoding: str = "",
    keep_alive: bool = False,
) -> bool:
    full_path = static_file_for_path(path)
    if not full_path:
        await send_http_error(writer, 404, "Not Found")
        return False

    try:
        stat_result = os.stat(full_path)
    except OSError:
        await send_http_error(writer, 404, "Not Found")
        return False
    if not stat.S_ISREG(stat_result.st_mode):
        await send_http_error(writer, 404, "Not Found")
        return False

    cache_key = full_path
    cached = STATIC_FILE_CACHE.get(cache_key)
    if cached and cached[0] == stat_result.st_mtime_ns and cached[1] == stat_result.st_size:
        body = cached[2]
        content_type = cached[3]
        gzipped_body = cached[4]
    else:
        with open(full_path, "rb") as file:
            body = file.read()

        content_type = static_content_type(full_path)
        gzipped_body = None
        STATIC_FILE_CACHE[cache_key] = (stat_result.st_mtime_ns, stat_result.st_size, body, content_type, gzipped_body)

    response_body = body
    content_encoding = None
    can_gzip = len(body) >= 1024 and is_gzippable_content_type(content_type)
    if accepts_gzip(accept_encoding) and can_gzip:
        if gzipped_body is None:
            # Compress off the event loop so a cache-miss on a large asset does
            # not block other connections; startup prewarm covers the common case.
            gzipped_body = await asyncio.to_thread(gzip.compress, body)
            STATIC_FILE_CACHE[cache_key] = (
                stat_result.st_mtime_ns,
                stat_result.st_size,
                body,
                content_type,
                gzipped_body,
            )
        response_body = gzipped_body
        content_encoding = "gzip"

    await send_http_response(
        writer,
        200,
        "OK",
        response_body,
        content_type,
        content_encoding=content_encoding,
        cache_control=cache_control_for_path(path),
        vary_accept_encoding=can_gzip,
        keep_alive=keep_alive,
    )
    return keep_alive


async def complete_handshake(
    writer: asyncio.StreamWriter,
    config: GatewayConfig,
    request_line: str,
    headers: dict[str, str],
) -> bool:
    if not validate_same_origin(headers):
        await send_http_error(writer, 403, "Forbidden")
        return False

    upgrade = headers.get("upgrade", "").lower()
    connection = headers.get("connection", "").lower()
    key = headers.get("sec-websocket-key")
    protocol = selected_subprotocol(headers)

    if upgrade != "websocket" or "upgrade" not in connection or not key:
        await send_http_error(writer, 400, "Bad Request")
        return False

    accept_key = websocket_accept_key(key)
    protocol_header = f"Sec-WebSocket-Protocol: {protocol}\r\n" if protocol else ""
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key}\r\n"
        f"{protocol_header}"
        "\r\n"
    )
    writer.write(response.encode("ascii"))
    await writer.drain()
    return True


async def read_exactly(reader: asyncio.StreamReader, length: int) -> bytes:
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise WebSocketProtocolError("connection closed") from exc


class WebSocketFrameReader:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader
        self.fragment_opcode: Optional[int] = None
        self.fragments: list[bytes] = []
        self.fragment_size = 0

    async def read(self) -> Optional[tuple[int, bytes]]:
        while True:
            fin, opcode, payload = await self._read_raw_frame()

            if opcode in (0x8, 0x9, 0xA):
                if not fin:
                    raise WebSocketProtocolError("fragmented control frame")
                if len(payload) > 125:
                    raise WebSocketProtocolError("control frame too large")
                if opcode == 0x8:
                    return None
                return opcode, payload

            if opcode in (0x1, 0x2):
                if self.fragment_opcode is not None:
                    raise WebSocketProtocolError("new data frame before fragmented message completed")
                if fin:
                    return opcode, payload
                self.fragment_opcode = opcode
                self.fragments = [payload]
                self.fragment_size = len(payload)
                continue

            if opcode == 0x0:
                if self.fragment_opcode is None:
                    raise WebSocketProtocolError("unexpected continuation frame")
                self.fragments.append(payload)
                self.fragment_size += len(payload)
                if self.fragment_size > MAX_FRAME_BYTES:
                    raise WebSocketProtocolError("frame too large")
                if not fin:
                    continue
                message = b"".join(self.fragments)
                message_opcode = self.fragment_opcode
                self.fragment_opcode = None
                self.fragments = []
                self.fragment_size = 0
                return message_opcode, message

            raise WebSocketProtocolError(f"unsupported opcode {opcode}")

    async def _read_raw_frame(self) -> tuple[bool, int, bytes]:
        header = await read_exactly(self.reader, 2)
        first, second = header[0], header[1]
        fin = (first & 0x80) != 0
        rsv = first & 0x70
        opcode = first & 0x0F
        masked = (second & 0x80) != 0
        length = second & 0x7F

        if rsv:
            raise WebSocketProtocolError("reserved websocket bits set")
        if length == 126:
            length = struct.unpack("!H", await read_exactly(self.reader, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await read_exactly(self.reader, 8))[0]

        if length > MAX_FRAME_BYTES:
            raise WebSocketProtocolError("frame too large")

        mask = await read_exactly(self.reader, 4) if masked else b""
        payload = await read_exactly(self.reader, length)

        if masked:
            payload = unmask_ws_payload(payload, mask)

        return fin, opcode, payload


def unmask_ws_payload(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return payload

    length = len(payload)
    full_mask = mask * (length // 4) + mask[: length % 4]
    return (
        int.from_bytes(payload, "big") ^ int.from_bytes(full_mask, "big")
    ).to_bytes(length, "big")


def decode_binary_input_payload(payload: bytes) -> tuple[bool, bytes]:
    # Binary input frames are [0x00][raw bytes]; return the raw terminal bytes.
    if not payload or payload[0] != BINARY_TYPE_DATA:
        return False, b""
    return True, payload[1:]


async def send_ws_frame(writer: asyncio.StreamWriter, payload: bytes, opcode: int = 0x1) -> None:
    writer.write(encode_ws_frame(payload, opcode))
    await asyncio.wait_for(writer.drain(), timeout=WEBSOCKET_DRAIN_TIMEOUT_SECONDS)


def encode_json_payload(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def encode_ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    first = 0x80 | opcode
    length = len(payload)

    if length < 126:
        header = struct.pack("!BB", first, length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, 126, length)
    else:
        header = struct.pack("!BBQ", first, 127, length)

    return header + payload


async def _drain_if_buffered(writer: asyncio.StreamWriter) -> None:
    # Avoid per-frame wait_for() overhead when the transport has already accepted
    # the data; retain timeout backpressure when a slow client builds a buffer.
    transport = writer.transport
    if transport is None or transport.get_write_buffer_size() == 0:
        return
    await asyncio.wait_for(writer.drain(), timeout=WEBSOCKET_DRAIN_TIMEOUT_SECONDS)


async def send_preencoded_ws_frame(writer: asyncio.StreamWriter, frame: bytes) -> None:
    writer.write(frame)
    await _drain_if_buffered(writer)


async def send_preencoded_ws_frames(writer: asyncio.StreamWriter, frames: list[bytes]) -> None:
    if not frames:
        return
    for frame in frames:
        writer.write(frame)
    await _drain_if_buffered(writer)


def encode_json_frame(payload: dict) -> bytes:
    return encode_ws_frame(encode_json_payload(payload))


async def send_json(writer: asyncio.StreamWriter, payload: dict) -> None:
    await send_preencoded_ws_frame(writer, encode_json_frame(payload))


def encode_pty_output_frame(data: bytes, sequence: Optional[int] = None) -> bytes:
    # Binary data frame: [0x00][uint32 BE sequence][raw bytes]. The sequence lets
    # the client de-duplicate replayed chunks on reconnect; 0 means "unsequenced".
    seq = (sequence or 0) & 0xFFFFFFFF
    payload = BINARY_TYPE_DATA_BYTE + struct.pack("!I", seq) + data
    return encode_ws_frame(payload, opcode=0x2)


async def close_stream_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(
            writer.wait_closed(),
            timeout=ATTACHED_WRITER_CLOSE_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, ConnectionError, OSError, RuntimeError):
        pass


def set_pty_size(fd: int, rows: int, cols: int) -> None:
    if fcntl is None or termios is None:
        return
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def spawn_shell(config: GatewayConfig, cols: Optional[int] = None, rows: Optional[int] = None) -> tuple[int, int]:
    if pty is None:
        raise RuntimeError("PTY mode requires a Unix-like server.")

    login_banner = build_login_banner()
    args = login_shell_args(config.shell)
    home_dir = shell_start_directory()
    initial_cols = cols or config.cols
    initial_rows = rows or config.rows

    pid, fd = pty.fork()

    if pid == 0:
        os.environ.setdefault("TERM", "xterm-256color")
        os.environ.setdefault("COLORTERM", "truecolor")
        set_pty_size(1, rows=initial_rows, cols=initial_cols)

        try:
            os.chdir(home_dir)
        except OSError:
            pass

        if login_banner:
            os.write(1, login_banner)

        os.execvp(args[0], args)

    set_pty_size(fd, initial_rows, initial_cols)
    os.set_blocking(fd, False)
    return pid, fd


def shell_start_directory() -> str:
    return os.environ.get("HOME") or os.path.expanduser("~")


def login_shell_args(shell: str) -> list[str]:
    args = shlex.split(shell)
    if not args:
        args = [os.environ.get("SHELL", "/bin/bash")]

    executable = args[0]
    if len(args) == 1 and os.path.basename(executable) == "bash":
        return [executable, "--login"]
    return args


_LOGIN_BANNER_CACHE: Optional[bytes] = None


def build_login_banner() -> bytes:
    # The banner (motd + `last` lookup) is static for the process lifetime, but
    # last_login_line() spawns a blocking `last` subprocess (up to 2s). It is
    # built inside spawn_shell() on the event-loop thread for every new session,
    # so recomputing it would stall ALL connections on each connect. Compute once.
    global _LOGIN_BANNER_CACHE
    if _LOGIN_BANNER_CACHE is not None:
        return _LOGIN_BANNER_CACHE

    chunks: list[str] = []

    dynamic_motd = read_text_if_present("/run/motd.dynamic") or read_text_if_present("/var/run/motd.dynamic")
    if dynamic_motd:
        chunks.append(dynamic_motd.rstrip("\n"))

    static_motd = read_text_if_present("/etc/motd")
    if static_motd:
        chunks.append(static_motd.strip("\n"))

    last_login = last_login_line()
    if last_login:
        chunks.append(last_login.rstrip("\n"))

    _LOGIN_BANNER_CACHE = (
        ("\n\n".join(chunks) + "\n").encode("utf-8", errors="replace") if chunks else b""
    )
    return _LOGIN_BANNER_CACHE


async def current_working_directory(probe: CwdProbe) -> str:
    tmux_cwd = await tmux_current_working_directory(probe.tmux_command)
    if tmux_cwd:
        return tmux_cwd

    proc_cwd = process_current_working_directory(probe.pid)
    if proc_cwd:
        return proc_cwd

    return ""


def initial_working_directory(probe: CwdProbe, fallback_cwd: str) -> str:
    if probe.tmux_command:
        result = run_tmux_cwd_command(list(probe.tmux_command))
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

    if fallback_cwd:
        return fallback_cwd

    return process_current_working_directory(probe.pid)


def process_current_working_directory(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def build_cwd_probe(pid: int, shell: str) -> CwdProbe:
    args = shlex.split(shell)
    if not args or os.path.basename(args[0]) != "tmux":
        return CwdProbe(pid=pid, tmux_command=None)

    command = args[:1] + ["display-message", "-p"]
    target = tmux_target_from_args(args[1:])
    if target:
        command.extend(["-t", target])
    command.append("#{pane_current_path}")
    return CwdProbe(pid=pid, tmux_command=tuple(command))


async def tmux_current_working_directory(command: Optional[tuple[str, ...]]) -> str:
    if not command:
        return ""

    result = await asyncio.to_thread(run_tmux_cwd_command, list(command))

    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def run_tmux_cwd_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=CWD_POLL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")


def tmux_target_from_args(args: list[str]) -> str:
    for index, arg in enumerate(args):
        if arg in ("-s", "-t") and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("-s") and len(arg) > 2:
            return arg[2:]
        if arg.startswith("-t") and len(arg) > 2:
            return arg[2:]
    return ""


def read_text_if_present(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as file:
            return file.read()
    except OSError:
        return ""


def last_login_line() -> str:
    user = current_user_name()
    try:
        result = subprocess.run(
            ["last", "-n", "1", "-w", user],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[0] != user:
            continue

        host = parts[2]
        timestamp = " ".join(parts[3:7])
        year = time.localtime().tm_year
        if host.startswith(":"):
            return f"Last login: {timestamp} {year}"
        return f"Last login: {timestamp} {year} from {host}"

    return ""


def current_user_name() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "root")


def read_pty_once(fd: int) -> bytes:
    try:
        return os.read(fd, PTY_READ_CHUNK_BYTES)
    except BlockingIOError:
        return b""


def read_pty_some(fd: int, max_bytes: int) -> bytes:
    try:
        return os.read(fd, max_bytes)
    except BlockingIOError:
        return b""


def read_pty_burst(fd: int, cap: int) -> bytes:
    # Concatenate consecutive immediately-available reads into one chunk, never
    # exceeding `cap` bytes. Never blocks: stops as soon as a read yields nothing.
    # `cap` MUST be <= SESSION_OUTPUT_BUFFER_BYTES so the chunk survives
    # _trim_output_buffer() and stays available for replay.
    first = read_pty_once(fd)
    if not first:
        return first
    parts = [first]
    total = len(first)
    while total < cap:
        try:
            more = read_pty_some(fd, cap - total)
        except OSError:
            break
        if not more:
            break
        parts.append(more)
        total += len(more)
    return parts[0] if len(parts) == 1 else b"".join(parts)


def child_exit_code(pid: int) -> Optional[int]:
    try:
        ended_pid, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return 0

    if ended_pid != pid:
        return None
    return os.waitstatus_to_exitcode(status)


def parse_resize(message: dict) -> tuple[int, int]:
    try:
        cols = int(message.get("cols", 100))
        rows = int(message.get("rows", 30))
    except (TypeError, ValueError) as exc:
        raise WebSocketProtocolError("invalid resize dimensions") from exc

    return min(max(cols, 20), 1000), min(max(rows, 10), 1000)


async def wait_for_fd_writable(fd: int) -> None:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def ready() -> None:
        if not future.done():
            future.set_result(None)

    loop.add_writer(fd, ready)
    try:
        await asyncio.wait_for(future, timeout=PTY_WRITE_TIMEOUT_SECONDS)
    finally:
        loop.remove_writer(fd)


async def wait_for_fd_readable(fd: int) -> None:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def ready() -> None:
        if not future.done():
            future.set_result(None)

    loop.add_reader(fd, ready)
    try:
        await future
    finally:
        loop.remove_reader(fd)


async def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    total = 0

    while total < len(data):
        try:
            written = os.write(fd, view[total:])
        except BlockingIOError:
            await wait_for_fd_writable(fd)
            continue
        if written == 0:
            raise OSError("PTY write returned zero bytes")
        total += written


class PersistentSession:
    def __init__(
        self,
        session_id: str,
        config: GatewayConfig,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> None:
        self.session_id = session_id
        self.config = config
        self.cols = cols or config.cols
        self.rows = rows or config.rows
        self.pid, self.fd = spawn_shell(config, cols=self.cols, rows=self.rows)
        self.created_at = time.time()
        self.last_detach_at = self.created_at
        self.exit_code: Optional[int] = None
        self.closed = False
        self.resources_closed = False
        self.output_sequence = 0
        self.output_buffer: deque[OutputChunk] = deque()
        self.output_buffer_size = 0
        self.cwd_probe = build_cwd_probe(self.pid, self.config.shell)
        self.current_cwd = initial_working_directory(self.cwd_probe, shell_start_directory())
        self.cwd_polling_active = asyncio.Event()
        self.cwd_probe_task: Optional[asyncio.Task[str]] = None
        self.attachments: dict[asyncio.StreamWriter, SessionWriter] = {}
        self.ready_attachment_snapshot: Optional[list[tuple[asyncio.StreamWriter, SessionWriter]]] = None
        self.attach_lock = asyncio.Lock()
        self.reader_task = asyncio.create_task(self._reader_loop())
        self.cwd_task = asyncio.create_task(self._cwd_loop())

    async def attach(self, writer: asyncio.StreamWriter, peer: object) -> None:
        refresh_cwd = False
        async with self.attach_lock:
            if self.closed:
                raise SessionAttachError("session is closed")

            refresh_cwd = not self.attachments
            # Enforce the current replay cap before exposing buffered history.
            self._trim_output_buffer()
            replay_until_sequence = self.output_sequence
            replay_chunks, replay_bytes = self._recent_replay(replay_until_sequence)
            replay_chunk_count = len(replay_chunks)
            exit_code = self.exit_code
            session_writer = SessionWriter(lock=asyncio.Lock())
            self.attachments[writer] = session_writer
            self._invalidate_attachment_snapshot()
            if not refresh_cwd:
                self.cwd_polling_active.set()

        if refresh_cwd:
            cwd = await self._probe_current_cwd()
            if cwd:
                self.current_cwd = cwd
            if self._is_attached(writer, session_writer):
                self.cwd_polling_active.set()

        try:
            async with session_writer.lock:
                if not self._is_attached(writer, session_writer):
                    return
                await send_json(
                    writer,
                    {
                        "type": "hello",
                        "shell": self.config.shell,
                        "cols": self.cols,
                        "rows": self.rows,
                        "peer": str(peer),
                        "session": self.session_id,
                        "clients": len(self.attachments),
                        "persistent": True,
                        "createdAt": self.created_at,
                        "cwd": self.current_cwd,
                        "replayChunks": replay_chunk_count,
                        "replayBytes": replay_bytes,
                    },
                )
                replay_frames: list[bytes] = []
                for chunk in replay_chunks:
                    if not self._is_attached(writer, session_writer):
                        return
                    if chunk.sequence <= replay_until_sequence:
                        replay_frames.append(chunk.encoded_frame())
                if exit_code is not None:
                    if not self._is_attached(writer, session_writer):
                        return
                    replay_frames.append(encode_json_frame({"type": "exit", "code": exit_code}))
                await send_preencoded_ws_frames(writer, replay_frames)
                await self._send_attach_catchup(
                    writer,
                    session_writer,
                    replay_until_sequence,
                    exit_sent=exit_code is not None,
                )
        except (asyncio.TimeoutError, ConnectionError, OSError, RuntimeError):
            self.detach(writer)
            raise

    def detach(self, writer: asyncio.StreamWriter) -> None:
        if writer in self.attachments:
            del self.attachments[writer]
            self._invalidate_attachment_snapshot()
            if not self.attachments:
                self.last_detach_at = time.time()
                self.cwd_polling_active.clear()

    def _is_attached(self, writer: asyncio.StreamWriter, session_writer: SessionWriter) -> bool:
        return self.attachments.get(writer) is session_writer

    def _invalidate_attachment_snapshot(self) -> None:
        self.ready_attachment_snapshot = None

    def _mark_attachment_ready(self, session_writer: SessionWriter) -> None:
        if not session_writer.ready:
            session_writer.ready = True
            self._invalidate_attachment_snapshot()

    def _ready_attachment_snapshot(self) -> list[tuple[asyncio.StreamWriter, SessionWriter]]:
        snapshot = self.ready_attachment_snapshot
        if snapshot is None:
            snapshot = [
                (writer, session_writer)
                for writer, session_writer in self.attachments.items()
                if session_writer.ready
            ]
            self.ready_attachment_snapshot = snapshot
        return snapshot

    def _detach_failed_writers(
        self,
        attachments: list[tuple[asyncio.StreamWriter, SessionWriter]],
        results: list[object],
    ) -> None:
        for (writer, _), result in zip(attachments, results):
            if isinstance(result, (asyncio.TimeoutError, ConnectionError, OSError, RuntimeError)):
                self.detach(writer)

        if not self.attachments:
            self.last_detach_at = time.time()

    def is_idle_expired(self, now: float) -> bool:
        return not self.attachments and (now - self.last_detach_at) > SESSION_IDLE_TIMEOUT_SECONDS

    def _trim_output_buffer(self) -> None:
        while self.output_buffer_size > SESSION_OUTPUT_BUFFER_BYTES and self.output_buffer:
            removed = self.output_buffer.popleft()
            self.output_buffer_size -= removed.raw_length

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        set_pty_size(self.fd, rows=rows, cols=cols)

    async def write_input(self, data: bytes) -> None:
        await write_all(self.fd, data)

    async def close(self) -> None:
        self.closed = True

        await self._close_attached_writers()

        if self.reader_task is not asyncio.current_task():
            self.reader_task.cancel()
            try:
                await self.reader_task
            except asyncio.CancelledError:
                pass
            except OSError:
                pass

        if self.cwd_task is not asyncio.current_task():
            self.cwd_task.cancel()
            try:
                await self.cwd_task
            except asyncio.CancelledError:
                pass
            except OSError:
                pass

        self._close_process_resources(terminate=True)
        await self._reap_terminated_child()

    async def _reap_terminated_child(self) -> None:
        # terminate=True sent SIGHUP but the child usually hasn't exited yet, so
        # the single WNOHANG inside _close_process_resources races and misses it,
        # leaving a zombie nobody reaps (the reader loop is gone). Poll briefly,
        # without blocking the event loop.
        for _ in range(50):
            try:
                reaped, _status = os.waitpid(self.pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                return
            if reaped == self.pid:
                return
            await asyncio.sleep(0.01)

    async def _close_attached_writers(self) -> None:
        writers = list(self.attachments)
        if not writers:
            return

        self.attachments.clear()
        self._invalidate_attachment_snapshot()
        self.last_detach_at = time.time()
        await asyncio.gather(*(close_stream_writer(writer) for writer in writers), return_exceptions=True)

    async def _reader_loop(self) -> None:
        while not self.closed:
            try:
                data = read_pty_burst(self.fd, SESSION_OUTPUT_BUFFER_BYTES)
            except OSError:
                data = b""

            if data:
                chunk = self._append_output(data)
                await self._send_to_attached(chunk)
                continue

            exit_code = child_exit_code(self.pid)
            if exit_code is not None:
                await self._drain_after_exit()
                self.exit_code = exit_code
                await self._send_exit_to_attached(exit_code)
                self.closed = True
                await self._close_attached_writers()
                self._close_process_resources(terminate=False)
                break

            await wait_for_fd_readable(self.fd)

    async def _cwd_loop(self) -> None:
        while not self.closed:
            await self.cwd_polling_active.wait()
            interval = CWD_POLL_INTERVAL_SECONDS
            while not self.closed and self.cwd_polling_active.is_set():
                cwd = await self._probe_current_cwd()
                if cwd != self.current_cwd:
                    self.current_cwd = cwd
                    interval = CWD_POLL_INTERVAL_SECONDS
                    if cwd:
                        await self._send_cwd_to_attached(cwd)
                else:
                    interval = min(interval * 1.5, CWD_POLL_MAX_INTERVAL_SECONDS)
                await asyncio.sleep(interval)

    async def _probe_current_cwd(self) -> str:
        task = self.cwd_probe_task
        if task is None or task.done():
            task = asyncio.create_task(current_working_directory(self.cwd_probe))
            self.cwd_probe_task = task

        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self.cwd_probe_task is task:
                self.cwd_probe_task = None

    async def _drain_after_exit(self, idle_reads: int = 3) -> None:
        empty_reads = 0

        while empty_reads < idle_reads:
            try:
                data = read_pty_burst(self.fd, SESSION_OUTPUT_BUFFER_BYTES)
            except OSError:
                break
            if data:
                empty_reads = 0
                chunk = self._append_output(data)
                await self._send_to_attached(chunk)
                continue
            empty_reads += 1
            if empty_reads < idle_reads:
                await wait_for_fd_readable(self.fd)

    def _recent_replay(self, replay_until_sequence: int) -> tuple[list[OutputChunk], int]:
        replay_size = 0
        selected: list[OutputChunk] = []

        for chunk in reversed(self.output_buffer):
            if chunk.sequence > replay_until_sequence:
                continue
            if replay_size >= SESSION_REPLAY_BYTES:
                break
            selected.append(chunk)
            replay_size += chunk.raw_length

        selected.reverse()
        return selected, replay_size

    def _append_output(self, data: bytes) -> OutputChunk:
        self.output_sequence += 1
        sequence = self.output_sequence
        chunk = OutputChunk(sequence=sequence, data=data, raw_length=len(data), frame=b"")
        self.output_buffer.append(chunk)
        self.output_buffer_size += chunk.raw_length
        self._trim_output_buffer()
        return chunk

    async def _send_attach_catchup(
        self,
        writer: asyncio.StreamWriter,
        session_writer: SessionWriter,
        after_sequence: int,
        exit_sent: bool,
    ) -> None:
        last_sequence = after_sequence

        while True:
            if not self._is_attached(writer, session_writer):
                return

            frames: list[bytes] = []
            for chunk in self.output_buffer:
                if chunk.sequence > last_sequence:
                    frames.append(chunk.encoded_frame())
                    last_sequence = chunk.sequence
            if frames:
                await send_preencoded_ws_frames(writer, frames)
                continue

            if self.exit_code is not None:
                if not exit_sent:
                    await send_preencoded_ws_frame(writer, encode_json_frame({"type": "exit", "code": self.exit_code}))
                self._mark_attachment_ready(session_writer)
                return

            if last_sequence >= self.output_sequence:
                self._mark_attachment_ready(session_writer)
                return

            last_sequence = self.output_sequence

    async def _send_to_attached(self, chunk: OutputChunk) -> None:
        if not self.attachments:
            return
        await self._broadcast_frame(chunk.encoded_frame())

    async def _send_cwd_to_attached(self, cwd: str) -> None:
        if not self.attachments:
            return
        await self._broadcast_frame(encode_json_frame({"type": "cwd", "cwd": cwd}))

    async def _send_exit_to_attached(self, exit_code: int) -> None:
        if not self.attachments:
            return
        await self._broadcast_frame(encode_json_frame({"type": "exit", "code": exit_code}))

    async def _broadcast_frame(self, frame: bytes) -> None:
        attachments = self._ready_attachment_snapshot()
        if not attachments:
            return
        if len(attachments) == 1:
            writer, session_writer = attachments[0]
            try:
                result = await self._send_frame(writer, session_writer, frame)
            except Exception as exc:
                result = exc
            results = [result]
        else:
            results = await asyncio.gather(
                *(self._send_frame(writer, session_writer, frame) for writer, session_writer in attachments),
                return_exceptions=True,
            )
        self._detach_failed_writers(attachments, results)

    async def _send_frame(self, writer: asyncio.StreamWriter, session_writer: SessionWriter, frame: bytes) -> None:
        async with session_writer.lock:
            if not self._is_attached(writer, session_writer) or not session_writer.ready:
                return
            await send_preencoded_ws_frame(writer, frame)

    def _close_process_resources(self, terminate: bool) -> None:
        if self.resources_closed:
            return
        self.resources_closed = True

        try:
            os.close(self.fd)
        except OSError:
            pass

        if terminate:
            killed_group = False
            try:
                os.killpg(self.pid, signal.SIGHUP)
                killed_group = True
            except OSError:
                pass
            try:
                if not killed_group:
                    os.kill(self.pid, signal.SIGHUP)
            except OSError:
                pass

        try:
            os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pass
        except OSError:
            pass


class SessionManager:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.sessions: dict[str, PersistentSession] = {}
        self.lock = asyncio.Lock()
        self.cleanup_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self.cleanup_task is None:
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def get(self, session_id: str) -> PersistentSession:
        old_session: Optional[PersistentSession] = None
        async with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                session = PersistentSession(session_id, self.config)
                self.sessions[session_id] = session
            elif session.closed:
                old_session = session
                session = PersistentSession(session_id, self.config)
                self.sessions[session_id] = session

        if old_session is not None:
            await old_session.close()
        return session

    async def restart_session(
        self,
        session_id: str,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> PersistentSession:
        async with self.lock:
            old_session = self.sessions.pop(session_id, None)

        if old_session is not None:
            await old_session.close()

        session = PersistentSession(session_id, self.config, cols=cols, rows=rows)
        async with self.lock:
            replaced_session = self.sessions.get(session_id)
            self.sessions[session_id] = session

        if replaced_session is not None:
            await replaced_session.close()
        return session

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired: list[PersistentSession] = []

            async with self.lock:
                for session_id, session in self.sessions.items():
                    if session.closed or session.is_idle_expired(now):
                        expired.append(session)
                for session in expired:
                    session_id = session.session_id
                    self.sessions.pop(session_id, None)

            for session in expired:
                await session.close()


@dataclass(slots=True)
class SessionAttachment:
    session: PersistentSession


async def socket_to_persistent_session(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    attachment: SessionAttachment,
    session_manager: SessionManager,
    stop: asyncio.Event,
) -> None:
    ws_reader = WebSocketFrameReader(reader)
    while not stop.is_set():
        session = attachment.session
        frame = await ws_reader.read()
        if frame is None:
            stop.set()
            break

        opcode, payload = frame
        if opcode == 0x9:
            await send_ws_frame(writer, payload, opcode=0xA)
            continue

        if opcode == 0x2:
            matched_input, data = decode_binary_input_payload(payload)
            if matched_input:
                try:
                    await session.write_input(data)
                except OSError:
                    await send_json(writer, {"type": "error", "message": "PTY input failed"})
                    stop.set()
                    break
            continue

        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await send_json(writer, {"type": "error", "message": "invalid JSON message"})
            continue

        message_type = message.get("type")
        if message_type == "input":
            try:
                data = base64.b64decode(message.get("data", ""), validate=True)
            except (binascii.Error, TypeError):
                await send_json(writer, {"type": "error", "message": "invalid base64 input"})
                continue
            try:
                await session.write_input(data)
            except OSError:
                await send_json(writer, {"type": "error", "message": "PTY input failed"})
                stop.set()
                break
        elif message_type == "resize":
            try:
                cols, rows = parse_resize(message)
            except WebSocketProtocolError as exc:
                await send_json(writer, {"type": "error", "message": str(exc)})
                continue
            session.resize(cols, rows)
        elif message_type == "ping":
            await send_json(writer, {"type": "pong", "time": time.time()})
        elif message_type == "kill":
            await session.close()
            stop.set()
            break
        elif message_type == "restart":
            session_id = session.session_id
            peer = writer.get_extra_info("peername")
            try:
                cols, rows = parse_resize(message)
            except WebSocketProtocolError:
                cols, rows = session.config.cols, session.config.rows
            session.detach(writer)
            try:
                new_session = await session_manager.restart_session(session_id, cols=cols, rows=rows)
            except (OSError, RuntimeError) as exc:
                await session.attach(writer, peer)
                await send_json(writer, {"type": "error", "message": f"restart failed: {exc}"})
                continue
            attachment.session = new_session
            await new_session.attach(writer, peer)
        else:
            await send_json(writer, {"type": "error", "message": f"unknown message type: {message_type}"})


async def demo_output_loop(writer: asyncio.StreamWriter, stop: asyncio.Event) -> None:
    await send_json(
        writer,
        {
            "type": "output",
            "data": base64.b64encode(
                b"[demo] Windows preview mode is active.\n"
                b"[demo] Move this folder to a Linux server for real PTY execution.\n"
                b"[demo] Try: help, date, whoami, uname -a, sleep\n\n"
            ).decode("ascii"),
        },
    )

    tick = 1
    while not stop.is_set():
        await asyncio.sleep(5)
        if stop.is_set():
            break
        line = f"[demo stream] background output tick {tick} at {time.strftime('%H:%M:%S')}\n"
        await send_json(
            writer,
            {
                "type": "output",
                "data": base64.b64encode(line.encode("utf-8")).decode("ascii"),
            },
        )
        tick += 1


async def demo_socket_loop(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    stop: asyncio.Event,
) -> None:
    ws_reader = WebSocketFrameReader(reader)
    while not stop.is_set():
        frame = await ws_reader.read()
        if frame is None:
            stop.set()
            break

        opcode, payload = frame
        if opcode == 0x9:
            await send_ws_frame(writer, payload, opcode=0xA)
            continue

        if opcode == 0x2:
            matched_input, data = decode_binary_input_payload(payload)
            if matched_input:
                await send_demo_command_output(writer, data)
            continue

        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await send_json(writer, {"type": "error", "message": "invalid JSON message"})
            continue

        message_type = message.get("type")
        if message_type == "input":
            try:
                data = base64.b64decode(message.get("data", ""), validate=True)
            except (binascii.Error, TypeError):
                await send_json(writer, {"type": "error", "message": "invalid base64 input"})
                continue
            await send_demo_command_output(writer, data)
        elif message_type == "resize":
            try:
                cols, rows = parse_resize(message)
            except WebSocketProtocolError as exc:
                await send_json(writer, {"type": "error", "message": str(exc)})
                continue
            await send_demo_text(writer, f"[demo] resized viewport to {cols}x{rows}\n")
        elif message_type == "ping":
            await send_json(writer, {"type": "pong", "time": time.time()})
        elif message_type == "restart":
            await send_demo_text(writer, "[demo] restarting terminal\n")
            stop.set()
            break
        else:
            await send_json(writer, {"type": "error", "message": f"unknown message type: {message_type}"})


async def send_demo_command_output(writer: asyncio.StreamWriter, data: bytes) -> None:
    if data == b"\x03":
        await send_demo_text(writer, "^C\n[demo] interrupt received\n")
        return

    command = data.decode("utf-8", errors="replace").strip()
    if not command:
        return

    if command == "help":
        text = (
            "Demo commands:\n"
            "  help      show this list\n"
            "  date      show gateway time\n"
            "  whoami    show demo user\n"
            "  uname -a  show demo platform\n"
            "  sleep     stream delayed output\n"
        )
    elif command == "date":
        text = time.strftime("%Y-%m-%d %H:%M:%S %Z\n")
    elif command == "whoami":
        text = "server-console-demo\n"
    elif command == "uname -a":
        text = "DemoOS server-console 0.1 preview-websocket mobile-pwa\n"
    elif command == "sleep":
        await send_demo_text(writer, "[demo] sleeping")
        for _ in range(3):
            await asyncio.sleep(1)
            await send_demo_text(writer, ".")
        await send_demo_text(writer, " done\n")
        return
    else:
        text = f"[demo] received command: {command}\n"

    await send_demo_text(writer, text)


async def send_demo_text(writer: asyncio.StreamWriter, text: str) -> None:
    await send_json(
        writer,
        {
            "type": "output",
            "data": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        },
    )


async def handle_demo_session(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: GatewayConfig,
    peer: object,
) -> None:
    stop = asyncio.Event()

    try:
        await send_json(
            writer,
            {
                "type": "hello",
                "shell": "demo",
                "cols": config.cols,
                "rows": config.rows,
                "peer": str(peer),
            },
        )

        tasks = [
            asyncio.create_task(demo_output_loop(writer, stop)),
            asyncio.create_task(demo_socket_loop(reader, writer, stop)),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        stop.set()

        for task in pending:
            task.cancel()
        for task in done:
            try:
                task.result()
            except (WebSocketProtocolError, asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
    finally:
        stop.set()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: GatewayConfig,
    session_manager: Optional[SessionManager],
) -> None:
    peer = writer.get_extra_info("peername")
    requests_served = 0

    try:
        while True:
            try:
                request_line, headers = await read_http_request(reader)
                path = request_path(request_line)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, HttpRequestError, asyncio.TimeoutError):
                if requests_served > 0:
                    return
                await send_http_error(writer, 400, "Bad Request")
                return

            if path != "/terminal":
                requests_served += 1
                full_path = static_file_for_path(path)
                keep_alive = (
                    static_file_exists(full_path)
                    and not request_wants_close(request_line, headers)
                    and requests_served < MAX_KEEPALIVE_REQUESTS
                )
                can_continue = await serve_static_file(
                    writer,
                    path,
                    headers.get("accept-encoding", ""),
                    keep_alive=keep_alive,
                )
                if can_continue:
                    continue
                return

            if not await complete_handshake(writer, config, request_line, headers):
                return

            if config.mode == "demo":
                await handle_demo_session(reader, writer, config, peer)
                return

            stop = asyncio.Event()
            session_id = session_id_from_request(request_line)
            if session_manager is None:
                await send_json(writer, {"type": "error", "message": "session manager unavailable"})
                return

            try:
                session = await session_manager.get(session_id)
                attachment = SessionAttachment(session)
                await session.attach(writer, peer)
                await socket_to_persistent_session(reader, writer, attachment, session_manager, stop)
            except SessionAttachError as exc:
                await send_json(writer, {"type": "error", "message": str(exc)})
            except (
                WebSocketProtocolError,
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
                asyncio.IncompleteReadError,
            ) as exc:
                print(f"client {peer} disconnected: {exc}")
            finally:
                stop.set()
                if "attachment" in locals():
                    attachment.session.detach(writer)
            return
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def main_async(config: GatewayConfig) -> None:
    prewarm_static_cache()
    session_manager = SessionManager(config) if config.mode == "pty" else None
    if session_manager:
        session_manager.start()

    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, config, session_manager),
        config.host,
        config.port,
    )

    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"Server Console gateway listening on {addresses}")
    print(f"Mode: {config.mode}")
    print(f"Open app: http://{config.host}:{config.port}/")
    print("WebSocket path: /terminal?session=phone")

    async with server:
        await server.serve_forever()


def default_shell() -> str:
    return os.environ.get("SHELL", "/bin/bash")


def parse_args() -> GatewayConfig:
    parser = argparse.ArgumentParser(description="WebSocket to PTY gateway for Server Console")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", default=8765, type=int, help="bind port")
    parser.add_argument("--shell", default=default_shell(), help="shell command to run")
    parser.add_argument("--cols", default=100, type=int, help="initial terminal columns")
    parser.add_argument("--rows", default=30, type=int, help="initial terminal rows")
    parser.add_argument(
        "--mode",
        choices=("auto", "pty", "demo"),
        default="auto",
        help="auto uses PTY on Unix-like servers and demo mode on Windows",
    )
    args = parser.parse_args()

    mode = args.mode
    if mode == "auto":
        mode = "pty" if PTY_AVAILABLE else "demo"
    if mode == "pty" and not PTY_AVAILABLE:
        raise SystemExit("PTY mode requires a Unix-like server. Use --mode demo on Windows.")

    return GatewayConfig(
        host=args.host,
        port=args.port,
        shell=args.shell,
        cols=args.cols,
        rows=args.rows,
        mode=mode,
    )


def main() -> None:
    config = parse_args()
    try:
        asyncio.run(main_async(config))
    except KeyboardInterrupt:
        print("\nGateway stopped.")


if __name__ == "__main__":
    main()
