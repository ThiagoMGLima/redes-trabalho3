import json
import socket
import threading
import time
import unittest

import protocol as p


def send_in_pieces(sock, data, sizes, pause=0.02):
  """Envia `data` em pedaços de tamanhos controlados, com pausas entre eles."""
  def run():
    offset = 0
    for size in sizes:
      sock.sendall(data[offset:offset + size])
      offset += size
      time.sleep(pause)
    sock.sendall(data[offset:])
  thread = threading.Thread(target=run)
  thread.start()
  return thread


class FramingTest(unittest.TestCase):
  def setUp(self):
    self.near, self.far = socket.socketpair()
    self.addCleanup(self.near.close)
    self.addCleanup(self.far.close)

  def test_header_and_content_split_in_pieces_are_rebuilt_once(self):
    frame = p.encode(p.CHAT, {"text": "olá, mundo"})

    sender = send_in_pieces(self.far, frame, [1, 2, 3, 4])
    message = p.receive_message(self.near)
    sender.join()

    self.assertEqual(message, (p.CHAT, {"text": "olá, mundo"}))
    self.far.close()
    self.assertIsNone(p.receive_message(self.near))

  def test_frames_sent_together_are_separated_in_order(self):
    block = bytes(range(256)) * 4
    frames = (
      p.encode(p.FILE_META, {"size": 2048})
      + p.encode(p.FILE_DATA, block)
      + p.encode(p.CHAT, {"origin": "Servidor", "text": "durante o download"})
      + p.encode(p.FILE_DATA, block[::-1])
      + p.encode(p.QUIT, {})
    )

    self.far.sendall(frames)
    self.far.close()

    received = []
    while (message := p.receive_message(self.near)) is not None:
      received.append(message)
    self.assertEqual(received, [
      (p.FILE_META, {"size": 2048}),
      (p.FILE_DATA, block),
      (p.CHAT, {"origin": "Servidor", "text": "durante o download"}),
      (p.FILE_DATA, block[::-1]),
      (p.QUIT, {}),
    ])

  def test_file_data_carries_original_bytes_without_json_or_base64(self):
    block = b"\x00\xff{\"not\": json}\n" + bytes(range(256))

    frame = p.encode(p.FILE_DATA, block)

    self.assertEqual(frame[0], p.FILE_DATA)
    self.assertEqual(int.from_bytes(frame[1:5], "big"), len(block))
    self.assertEqual(frame[5:], block)

  def test_control_is_utf8_json_and_length_counts_encoded_bytes(self):
    text = "ação 😀 ok"

    frame = p.encode(p.CHAT, {"text": text})

    payload = frame[5:]
    self.assertEqual(frame[0], p.CHAT)
    self.assertEqual(int.from_bytes(frame[1:5], "big"), len(payload))
    self.assertEqual(json.loads(payload.decode("utf-8")), {"text": text})
    self.assertIn(text.encode("utf-8"), payload)
    self.assertGreater(len(payload), len(payload.decode("utf-8")))


class InvalidFrameTest(unittest.TestCase):
  def test_unknown_type_is_rejected_without_seeking_another_header(self):
    for unknown in (0, 99, 255):
      with self.subTest(type=unknown):
        near, far = socket.socketpair()
        with near, far:
          far.sendall(bytes([unknown]) + (2).to_bytes(4, "big") + b"{}" + p.encode(p.QUIT, {}))

          with self.assertRaises(p.InvalidFrame):
            p.receive_message(near, timeout=5)

  def test_control_that_is_not_valid_utf8_json_is_rejected(self):
    for payload in (b"{not json", b"", b"\xff\xfe{}", b"[" * (64 * 1024)):
      with self.subTest(payload=payload[:10]):
        near, far = socket.socketpair()
        with near, far:
          far.sendall(bytes([p.CHAT]) + len(payload).to_bytes(4, "big") + payload)

          with self.assertRaises(p.InvalidFrame):
            p.receive_message(near, timeout=5)

  def test_encoding_refuses_unknown_type(self):
    with self.assertRaises(ValueError):
      p.encode(99, {})


class StreamEndTest(unittest.TestCase):
  def receive_after_close(self, data):
    near, far = socket.socketpair()
    with near, far:
      far.sendall(data)
      far.close()
      return p.receive_message(near, timeout=5)

  def test_end_inside_header_is_an_incomplete_frame(self):
    with self.assertRaises(p.IncompleteFrame):
      self.receive_after_close(p.encode(p.QUIT, {})[:3])

  def test_end_inside_content_is_an_incomplete_frame(self):
    frame = p.encode(p.CHAT, {"text": "interrompida"})
    with self.assertRaises(p.IncompleteFrame):
      self.receive_after_close(frame[:-1])

  def test_end_between_frames_is_not_an_incomplete_frame(self):
    self.assertIsNone(self.receive_after_close(b""))

  def test_bytes_after_an_interrupted_content_are_not_taken_as_a_new_frame(self):
    quit_frame = p.encode(p.QUIT, {})
    announced = bytes([p.FILE_DATA]) + (100).to_bytes(4, "big")
    with self.assertRaises(p.IncompleteFrame):
      self.receive_after_close(announced + quit_frame)


class LimitsTest(unittest.TestCase):
  def setUp(self):
    self.near, self.far = socket.socketpair()
    self.addCleanup(self.near.close)
    self.addCleanup(self.far.close)

  def test_content_of_exactly_64_kib_is_accepted(self):
    block = b"\xab" * (64 * 1024)

    self.far.sendall(p.encode(p.FILE_DATA, block))

    self.assertEqual(p.receive_message(self.near), (p.FILE_DATA, block))

  def test_encoding_refuses_content_above_64_kib(self):
    with self.assertRaises(ValueError):
      p.encode(p.FILE_DATA, b"x" * (64 * 1024 + 1))
    with self.assertRaises(ValueError):
      p.encode(p.CHAT, {"text": "é" * (32 * 1024)})

  def test_announced_length_above_64_kib_is_rejected_before_the_content(self):
    self.far.sendall(bytes([p.FILE_DATA]) + (64 * 1024 + 1).to_bytes(4, "big"))

    with self.assertRaises(p.InvalidFrame):
      p.receive_message(self.near, timeout=5)

  def test_file_blocks_are_8_kib(self):
    self.assertEqual(p.CHUNK_SIZE, 8 * 1024)
    self.assertEqual(p.MAX_CONTENT, 64 * 1024)


if __name__ == "__main__":
  unittest.main()
