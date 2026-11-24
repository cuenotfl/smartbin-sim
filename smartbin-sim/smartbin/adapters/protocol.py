"""Binary frame protocol Jetson <-> ESP32 (arc42 8.7, ADR-004).

Frame layout (little endian):

    0xA5 | type u8 | seq u16 | length u32 | hcrc u8 | payload[length] | crc16 u16

hcrc = CRC-8 (poly 0x07, init 0x00) over type..length. It lets the receiver
reject a corrupted length at once instead of waiting for a bogus payload
(found by the noisy-line simulation).
crc16 = CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over type..payload,
header crc included.
The same layout must be implemented in the ESP32 firmware (C++); the test
vectors in tests/test_protocol.py are the contract.

Postel: we send exactly this format; on receive we resynchronise after
garbage, drop frames with a wrong CRC and keep frames of unknown type for the
caller to ignore.
"""
import binascii
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import List

START = 0xA5
PROTOCOL_VERSION = 1
HEADER = struct.Struct("<BBHIB")  # start, type, seq, length, header crc8
CRC = struct.Struct("<H")
MAX_PAYLOAD = 512 * 1024


class MsgType(IntEnum):
    # ESP32 -> Jetson
    HELLO = 0x01          # module_id u8, fw_major u8, fw_minor u8, protocol u8
    ITEM_DETECTED = 0x02  # source u8 (0 = opening, PIR saw a hand; 1 = handover from neighbour)
    IMAGE = 0x03          # jpeg bytes
    SORT_DONE = 0x04      # result u8 (0 ok, 1 held, >=2 error)
    FAULT = 0x05          # code u8, state u8
    HEARTBEAT = 0x06      # ESP32->Jetson: state u8, last_done_seq u16, last_done_result u8
                          # Jetson->ESP32: empty
    # Jetson -> ESP32
    CONFIG = 0x10         # fraction u8, position u8, routing u8
    SORT = 0x11           # direction u8, signal u8 (0 = AUS/no change)
    SIGNAL = 0x12         # pattern u8
    RESET_FAULT = 0x13    # -


class ModuleStateCode(IntEnum):
    INIT = 0
    BEREIT = 1
    ERKANNT = 2
    WARTEN = 3
    SORTIEREN = 4
    STOERUNG = 5


class FaultCode(IntEnum):
    TIMEOUT_SORT_CMD = 1      # no SORT received in time
    PIR_BLOCKED = 2           # hand stayed in opening too long
    BLOCKADE = 3              # servo did not reach end position
    HEARTBEAT_LOST = 4        # Jetson silent
    CAMERA = 5


# Wire codes for enums (stable, never renumber; only append)
FRACTION_CODES = {"PET": 0, "ALU": 1, "REST": 2}
DIRECTION_CODES = {"DIREKT": 0, "LINKS": 1, "RECHTS": 2, "HALTEN": 3}
SIGNAL_CODES = {"AUS": 0, "OK": 1, "FEHLWURF": 2, "UNBEKANNT": 3, "STOERUNG": 4}
ROUTING_CODES = {"neighbour_bin": 0, "via_flaps": 1}


def _invert(d):
    return {v: k for k, v in d.items()}


FRACTION_NAMES = _invert(FRACTION_CODES)
DIRECTION_NAMES = _invert(DIRECTION_CODES)
SIGNAL_NAMES = _invert(SIGNAL_CODES)
ROUTING_NAMES = _invert(ROUTING_CODES)


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE. binascii.crc_hqx implements exactly this (C speed)."""
    return binascii.crc_hqx(data, 0xFFFF)


def crc16_reference(data: bytes) -> int:
    """Bitwise reference implementation, mirrors the firmware's C++ code."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    type: int
    seq: int
    payload: bytes = b""


def encode(frame: Frame) -> bytes:
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    fields = struct.pack("<BHI", frame.type, frame.seq & 0xFFFF, len(frame.payload))
    header = bytes([START]) + fields + bytes([crc8(fields)])
    body = header[1:] + frame.payload
    return header + frame.payload + CRC.pack(crc16(body))


class FrameDecoder:
    """Streaming decoder: feed() arbitrary chunks, get complete frames."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes) -> List[Frame]:
        self._buf.extend(data)
        frames: List[Frame] = []
        while True:
            start = self._buf.find(bytes([START]))
            if start < 0:
                self._buf.clear()
                return frames
            if start > 0:
                del self._buf[:start]
            if len(self._buf) < HEADER.size:
                return frames
            _, mtype, seq, length, hcrc = HEADER.unpack_from(self._buf, 0)
            if length > MAX_PAYLOAD or hcrc != crc8(bytes(self._buf[1:HEADER.size - 1])):
                self.crc_errors += 1
                del self._buf[0]          # bogus start byte or broken header, resync
                continue
            total = HEADER.size + length + CRC.size
            if len(self._buf) < total:
                return frames
            body = bytes(self._buf[1:HEADER.size + length])
            (crc,) = CRC.unpack_from(self._buf, HEADER.size + length)
            if crc != crc16(body):
                self.crc_errors += 1
                del self._buf[0]          # resync on next start byte
                continue
            frames.append(Frame(mtype, seq, bytes(self._buf[HEADER.size:HEADER.size + length])))
            del self._buf[:total]
