import json
import socket
import threading
import time
import unittest

import protocol as p


def send_in_pieces(sock, data, sizes=(), pause=0.02):
  """Envia `data` em outra thread, em pedaços de tamanhos controlados e com
  pausas entre eles; o restante segue em um único envio."""
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
          sender = send_in_pieces(far, bytes([p.CHAT]) + len(payload).to_bytes(4, "big") + payload)

          with self.assertRaises(p.InvalidFrame):
            p.receive_message(near, timeout=5)
          sender.join()

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


class ReceiveDeadlineTest(unittest.TestCase):
  TIMEOUT = 0.3

  def setUp(self):
    self.near, self.far = socket.socketpair()
    self.addCleanup(self.near.close)
    self.addCleanup(self.far.close)

  def test_frame_started_and_not_finished_expires(self):
    self.far.sendall(p.encode(p.QUIT, {})[:1])

    start = time.monotonic()
    with self.assertRaises(p.FrameTimeout):
      p.receive_message(self.near, timeout=self.TIMEOUT)
    elapsed = time.monotonic() - start

    self.assertGreaterEqual(elapsed, self.TIMEOUT - 0.05)
    self.assertLess(elapsed, self.TIMEOUT + 0.5)

  def test_partial_progress_does_not_restart_the_deadline(self):
    frame = p.encode(p.CHAT, {"text": "devagar" * 4})
    stop = threading.Event()

    def drip():
      for byte in frame:
        if stop.wait(0.05):
          return
        self.far.sendall(bytes([byte]))
    dripper = threading.Thread(target=drip)
    dripper.start()
    self.addCleanup(dripper.join)
    self.addCleanup(stop.set)

    start = time.monotonic()
    with self.assertRaises(p.FrameTimeout):
      p.receive_message(self.near, timeout=self.TIMEOUT)
    elapsed = time.monotonic() - start

    self.assertLess(elapsed, self.TIMEOUT + 0.5)

  def test_idle_wait_before_a_frame_starts_does_not_expire(self):
    def send_later():
      time.sleep(self.TIMEOUT * 2)
      self.far.sendall(p.encode(p.QUIT, {}))
    sender = threading.Thread(target=send_later)
    sender.start()
    self.addCleanup(sender.join)

    self.assertEqual(p.receive_message(self.near, timeout=self.TIMEOUT), (p.QUIT, {}))

  def test_default_receive_deadline_is_10_seconds(self):
    self.assertEqual(p.RECEIVE_TIMEOUT, 10)


def small_buffer_tcp_pair():
  """Conexão TCP local com buffers pequenos, para que um frame de 64 KiB só
  avance quando o outro lado lê."""
  with socket.create_server(("127.0.0.1", 0)) as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sender = socket.socket()
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    sender.connect(listener.getsockname())
    receiver, _ = listener.accept()
  return sender, receiver


class SendDeadlineTest(unittest.TestCase):
  TIMEOUT = 0.3

  def setUp(self):
    self.sender, self.receiver = small_buffer_tcp_pair()
    self.addCleanup(self.sender.close)
    self.addCleanup(self.receiver.close)

  def test_message_sent_within_the_deadline_arrives_whole(self):
    block = bytes(range(256)) * 32

    p.send_message(self.sender, p.FILE_DATA, block, timeout=self.TIMEOUT)
    p.send_message(self.sender, p.QUIT, {}, timeout=self.TIMEOUT)

    self.assertEqual(p.receive_message(self.receiver), (p.FILE_DATA, block))
    self.assertEqual(p.receive_message(self.receiver), (p.QUIT, {}))

  def test_frame_blocked_by_a_peer_that_does_not_read_expires(self):
    block = b"\x00" * (64 * 1024)

    start = time.monotonic()
    with self.assertRaises(p.FrameTimeout):
      p.send_message(self.sender, p.FILE_DATA, block, timeout=self.TIMEOUT)
    elapsed = time.monotonic() - start

    self.assertGreaterEqual(elapsed, self.TIMEOUT - 0.05)
    self.assertLess(elapsed, self.TIMEOUT + 0.5)

  def test_partial_progress_does_not_restart_the_deadline(self):
    received = []
    stop = threading.Event()

    def read_slowly():
      while not stop.wait(0.02):
        try:
          received.append(len(self.receiver.recv(2048)))
        except OSError:
          return
    reader = threading.Thread(target=read_slowly)
    reader.start()
    self.addCleanup(reader.join)
    self.addCleanup(stop.set)

    start = time.monotonic()
    with self.assertRaises(p.FrameTimeout):
      p.send_message(self.sender, p.FILE_DATA, b"\x00" * (64 * 1024), timeout=self.TIMEOUT)
    elapsed = time.monotonic() - start
    stop.set()
    reader.join()

    # Os buffers pequenos guardam cerca de 9 KiB; receber bem mais que isso
    # mostra que o frame avançou durante o prazo, sem chegar ao fim.
    self.assertLess(elapsed, self.TIMEOUT + 0.5)
    self.assertGreater(sum(received), 16 * 1024)
    self.assertLess(sum(received), 64 * 1024 + 5)

  def test_default_send_deadline_is_5_seconds(self):
    self.assertEqual(p.SEND_TIMEOUT, 5)


class LimitsTest(unittest.TestCase):
  def setUp(self):
    self.near, self.far = socket.socketpair()
    self.addCleanup(self.near.close)
    self.addCleanup(self.far.close)

  def test_content_of_exactly_64_kib_is_accepted(self):
    block = b"\xab" * (64 * 1024)

    sender = send_in_pieces(self.far, p.encode(p.FILE_DATA, block))
    message = p.receive_message(self.near)
    sender.join()

    self.assertEqual(message, (p.FILE_DATA, block))

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
