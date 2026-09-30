import json
import socket
import struct
import time
import unittest

from todo_flow.unix_websocket import UnixWebSocket


class UnixWebSocketPollingTests(unittest.TestCase):
    def setUp(self):
        receiver, self.sender = socket.socketpair()
        self.addCleanup(receiver.close)
        self.addCleanup(self.sender.close)
        self.client = UnixWebSocket.__new__(UnixWebSocket)
        self.client.sock = receiver
        self.client.deadline = None
        self.client.max_bytes = 16000000
        self.client.buffer = bytearray()
        self.client._receive_deadline = None
        self.client._fragments = bytearray()
        self.client._fragment_active = False

    def idle(self):
        with self.assertRaises(TimeoutError):
            self.client.receive(timeout=0.01)

    def test_idle_polls_preserve_partial_header_payload_and_fragmented_utf8(self):
        expected = {"text": "한글" * 100}
        payload = json.dumps(expected, ensure_ascii=False).encode()
        first, second = payload[:201], payload[201:]
        frame = bytes([1, 126]) + struct.pack("!H", len(first)) + first
        position = 0
        for end in (1, 2, 3, 4, 30, len(frame)):
            self.sender.sendall(frame[position:end])
            self.idle()
            position = end
        self.sender.sendall(bytes([137, 1]) + b"p")
        self.idle()
        # The pong is a separate control frame, not part of the JSON message.
        self.assertEqual(self.sender.recv(64)[0], 138)
        tail = bytes([128, 126]) + struct.pack("!H", len(second)) + second
        self.sender.sendall(tail[:10])
        self.idle()
        self.sender.sendall(tail[10:])
        self.assertEqual(self.client.receive(timeout=1), expected)
        self.sender.sendall(b"\x81\x02{}")
        self.assertEqual(self.client.receive(timeout=1), {})

    def test_explicit_overall_deadline_still_limits_idle_polls(self):
        self.client.deadline = time.monotonic() - 1
        with self.assertRaises(TimeoutError):
            self.client.receive(timeout=10)

    def test_blocked_pong_is_connection_failure_not_an_idle_poll(self):
        self.client.sock.setblocking(False)
        while True:
            try:
                self.client.sock.send(b"x" * 65536)
            except BlockingIOError:
                break
        self.sender.sendall(b"\x89\x01p")
        with self.assertRaisesRegex(OSError, "pong delivery") as caught:
            self.client.receive(timeout=0.01)
        self.assertNotIsInstance(caught.exception, TimeoutError)
