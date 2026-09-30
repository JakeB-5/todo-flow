"""Bounded RFC6455 client for a host-owned local App Server Unix socket."""

import base64
import hashlib
import json
import os
import socket
import struct
import time


class UnixWebSocket:
    def __init__(self, path, *, deadline, max_bytes=16_000_000):
        self.deadline = deadline
        self.max_bytes = max_bytes
        self.buffer = bytearray()
        self._receive_deadline = None
        self._fragments = bytearray()
        self._fragment_active = False
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._timeout()
            self.sock.connect(str(path))
            key = base64.b64encode(os.urandom(16)).decode()
            self.sock.sendall(
                (
                    "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                    "Connection: Upgrade\r\nSec-WebSocket-Key: "
                    + key
                    + "\r\nSec-WebSocket-Version: 13\r\n\r\n"
                ).encode()
            )
            while b"\r\n\r\n" not in self.buffer:
                if len(self.buffer) > 16384:
                    raise ValueError("WebSocket upgrade headers exceed limit")
                self.buffer.extend(self._recv())
            header, _, tail = self.buffer.partition(b"\r\n\r\n")
            self.buffer[:] = tail
            if len(header) > 16384:
                raise ValueError("WebSocket upgrade headers exceed limit")
            lines = header.decode("ascii").split("\r\n")
            if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
                raise ValueError("App Server refused WebSocket upgrade")
            headers = {}
            for line in lines[1:]:
                name, value = line.split(":", 1)
                name = name.lower()
                if name in headers:
                    raise ValueError("Duplicate WebSocket upgrade header")
                headers[name] = value.strip()
            expected = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            if (
                headers.get("sec-websocket-accept") != expected
                or headers.get("upgrade", "").lower() != "websocket"
                or "upgrade"
                not in [token.strip().lower() for token in headers.get("connection", "").split(",")]
                or "sec-websocket-extensions" in headers
            ):
                raise ValueError("Invalid WebSocket upgrade response")
        except BaseException:
            self.sock.close()
            raise

    def _timeout(self):
        deadlines = [
            value for value in (self.deadline, self._receive_deadline) if value is not None
        ]
        if not deadlines:
            self.sock.settimeout(None)
            return
        remaining = min(deadlines) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("App Server deadline expired")
        self.sock.settimeout(remaining)

    def _recv(self):
        self._timeout()
        data = self.sock.recv(65536)
        if not data:
            raise EOFError("App Server connection closed")
        return data

    def _fill(self, length):
        while len(self.buffer) < length:
            self.buffer.extend(self._recv())

    def _send_frame(self, payload, opcode):
        if len(payload) > self.max_bytes:
            raise ValueError("App Server request exceeds limit")
        size = len(payload)
        mask = os.urandom(4)
        header = bytes((128 | opcode, 128 | (size if size < 126 else 126 if size < 65536 else 127)))
        if size >= 126:
            header += struct.pack("!H" if size < 65536 else "!Q", size)
        self._timeout()
        self.sock.sendall(
            header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        )

    def send(self, message):
        self._send_frame(json.dumps(message, allow_nan=False, ensure_ascii=False).encode(), 1)

    def receive(self, timeout=None):
        """Bound one poll, retaining incomplete frames/messages across idle polls."""
        self._receive_deadline = None if timeout is None else time.monotonic() + timeout
        try:
            return self._receive()
        finally:
            self._receive_deadline = None

    def _receive(self):
        while True:
            # Do not consume a header until the complete frame is buffered. An
            # idle timeout in any header/payload byte is not a lost connection.
            self._fill(2)
            first, second = self.buffer[:2]
            opcode, finished = first & 15, bool(first & 128)
            if first & 112 or second & 128:
                raise ValueError("Unsupported WebSocket flags or masked server frame")
            size = second & 127
            header_size = 2
            if size == 126:
                header_size = 4
                self._fill(header_size)
                size = struct.unpack("!H", self.buffer[2:4])[0]
                if size < 126:
                    raise ValueError("Noncanonical WebSocket frame length")
            elif size == 127:
                header_size = 10
                self._fill(header_size)
                size = struct.unpack("!Q", self.buffer[2:10])[0]
                if size < 65536 or size >= 2**63:
                    raise ValueError("Invalid WebSocket frame length")
            if size > self.max_bytes or (
                opcode < 8 and len(self._fragments) + size > self.max_bytes
            ):
                raise ValueError("App Server message exceeds limit")
            if opcode >= 8 and (not finished or size > 125):
                raise ValueError("Invalid WebSocket control frame")
            self._fill(header_size + size)
            payload = bytes(self.buffer[header_size : header_size + size])
            del self.buffer[: header_size + size]
            if opcode == 8:
                raise EOFError("App Server closed the WebSocket")
            if opcode == 9:
                try:
                    self._send_frame(payload, 10)
                except TimeoutError as error:
                    # A partial pong write is uncertain delivery, not an idle
                    # read poll. Let the caller reconcile a new connection.
                    raise OSError("WebSocket pong delivery was not confirmed") from error
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not self._fragment_active:
                self._fragment_active = True
            elif opcode != 0 or not self._fragment_active:
                raise ValueError("Unexpected WebSocket data frame")
            self._fragments.extend(payload)
            if finished:
                result = json.loads(self._fragments.decode("utf-8"))
                self._fragments.clear()
                self._fragment_active = False
                return result

    def close(self):
        self.sock.close()
