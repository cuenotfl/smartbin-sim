"""Contract tests for the serial protocol. The firmware (C++) must pass the
same vectors: see CONTRACT_VECTORS."""
import random

import pytest

from smartbin.adapters import protocol as p

# (frame, expected bytes as hex) - copy these into the firmware unit tests
CONTRACT_VECTORS = [
    (p.Frame(p.MsgType.HEARTBEAT, 0, b""), "a506000000000000cc35a9"),
    (p.Frame(p.MsgType.ITEM_DETECTED, 7, b"\x00"), "a5020700010000007000f454"),
    (p.Frame(p.MsgType.SORT, 0x1234, b"\x02\x02"), "a511341202000000d70202d206"),
    (p.Frame(p.MsgType.SIGNAL, 1, b"\x02"), "a5120100010000005502905e"),
]


def test_crc_check_value():
    # standard check value of CRC-16/CCITT-FALSE
    assert p.crc16(b"123456789") == 0x29B1
    assert p.crc16_reference(b"123456789") == 0x29B1


def test_fast_crc_equals_reference():
    rng = random.Random(1)
    for _ in range(50):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300)))
        assert p.crc16(data) == p.crc16_reference(data)


@pytest.mark.parametrize("frame,expected_hex", CONTRACT_VECTORS)
def test_contract_vectors(frame, expected_hex):
    raw = p.encode(frame)
    assert raw.hex() == expected_hex
    assert p.FrameDecoder().feed(raw) == [frame]


def test_roundtrip_and_streaming_in_small_chunks():
    frames = [p.Frame(p.MsgType.IMAGE, 3, bytes(range(256)) * 40),
              p.Frame(p.MsgType.SORT_DONE, 3, b"\x00"),
              p.Frame(p.MsgType.HEARTBEAT, 0)]
    stream = b"".join(p.encode(f) for f in frames)
    dec = p.FrameDecoder()
    out = []
    for i in range(0, len(stream), 7):
        out += dec.feed(stream[i:i + 7])
    assert out == frames


def test_garbage_before_frame_is_skipped():
    dec = p.FrameDecoder()
    f = p.Frame(p.MsgType.HEARTBEAT, 0)
    assert dec.feed(b"\x00\x13garbage\xa5" + p.encode(f)) == [f]


def test_corrupted_frame_is_dropped_and_decoder_resyncs():
    dec = p.FrameDecoder()
    good = p.Frame(p.MsgType.SORT_DONE, 9, b"\x00")
    bad = bytearray(p.encode(p.Frame(p.MsgType.SORT, 9, b"\x01")))
    bad[-3] ^= 0xFF
    assert dec.feed(bytes(bad) + p.encode(good)) == [good]
    assert dec.crc_errors == 1


def test_unknown_type_is_delivered_for_caller_to_ignore():
    dec = p.FrameDecoder()
    assert dec.feed(p.encode(p.Frame(0x7F, 1, b"future")))[0].type == 0x7F
