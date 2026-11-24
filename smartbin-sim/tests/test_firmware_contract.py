"""The C++ firmware must speak the same protocol (ADR-004). Without compiling
anything, check that its wire codes and test vectors match protocol.py and
CONTRACT_VECTORS, so a change on one side cannot silently miss the other."""
import os
import re

import pytest

from smartbin.adapters import protocol as p
from tests.test_protocol import CONTRACT_VECTORS

FW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware")


def read(*parts):
    with open(os.path.join(FW, *parts), encoding="utf-8") as fh:
        return fh.read()


def cpp_enum(source, namespace):
    """{NAME: value} of `namespace X { enum : uint8_t { A = 1, ... }; }`."""
    m = re.search(r"namespace %s\s*\{\s*enum[^{]*\{(.*?)\}" % namespace, source, re.S)
    assert m, "namespace %s not found in protocol.h" % namespace
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return {name: int(val, 0) for name, val in re.findall(r"(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", body)}


@pytest.fixture(scope="module")
def header():
    return read("lib", "smartbin", "src", "protocol.h")


@pytest.mark.parametrize("namespace,expected", [
    ("Msg", {m.name: int(m) for m in p.MsgType}),
    ("StateCode", {s.name: int(s) for s in p.ModuleStateCode}),
    ("FaultCode", {f.name: int(f) for f in p.FaultCode}),
    ("Fraction", p.FRACTION_CODES),
    ("Direction", p.DIRECTION_CODES),
    ("Signal", p.SIGNAL_CODES),
    ("Routing", {k.upper(): v for k, v in p.ROUTING_CODES.items()}),
])
def test_wire_codes_match(header, namespace, expected):
    assert cpp_enum(header, namespace) == dict(expected)


def test_frame_constants_match(header):
    assert re.search(r"START = 0x%X;" % p.START, header)
    assert re.search(r"PROTOCOL_VERSION = %d;" % p.PROTOCOL_VERSION, header)
    assert re.search(r"HEADER_SIZE = %d;" % p.HEADER.size, header)
    assert "MAX_PAYLOAD = %dUL * 1024UL" % (p.MAX_PAYLOAD // 1024) in header


def test_firmware_tests_use_the_contract_vectors():
    src = read("test", "test_protocol", "test_main.cpp")
    block = src.split("// CONTRACT_VECTORS BEGIN")[1].split("// CONTRACT_VECTORS END")[0]
    found = [(int(t, 16), int(s, 16), ph, fh) for t, s, ph, fh in re.findall(
        r'\{(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),\s*"([0-9a-f]*)",\s*"([0-9a-f]+)"\}', block)]
    expected = [(f.type, f.seq, f.payload.hex(), hexstr) for f, hexstr in CONTRACT_VECTORS]
    assert found == expected
