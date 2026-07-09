"""
test_protocol.py - Stage 1 verification.

Tests CRC16 correctness and Modbus RTU framing/parsing in complete
isolation - no serial port, no hardware, no queue/scheduler involved.
Run with: pytest tests/test_protocol.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from pi_engine.protocol.modbus_rtu import (
    ModbusRTU,
    crc16,
    append_crc,
    verify_crc,
    FC_READ_COILS,
    FC_READ_DISCRETE_INPUTS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    FC_WRITE_SINGLE_REGISTER,
    FC_WRITE_MULTIPLE_REGISTERS,
)
from pi_engine.protocol.base_protocol import ProtocolRequest


# ---------------------------------------------------------------------------
# CRC16 - known-good vectors
# ---------------------------------------------------------------------------

def test_crc16_known_vector():
    # Standard Modbus CRC16 test vector: slave 1, FC 03, addr 0x006B, qty 3
    # -> on-wire bytes are 01 03 00 6B 00 03 [CRC_lo=0x74] [CRC_hi=0x17]
    frame = bytes([0x01, 0x03, 0x00, 0x6B, 0x00, 0x03])
    crc = crc16(frame)
    assert crc == 0x1774, f"expected CRC 0x1774, got {crc:#06x}"
    framed = append_crc(frame)
    assert framed[-2:] == bytes([0x74, 0x17])  # low byte first on the wire


def test_append_crc_and_verify_roundtrip():
    frame = bytes([0x11, 0x03, 0x00, 0x00, 0x00, 0x01])
    framed = append_crc(frame)
    assert len(framed) == len(frame) + 2
    assert verify_crc(framed) is True


def test_verify_crc_detects_corruption():
    frame = bytes([0x11, 0x03, 0x00, 0x00, 0x00, 0x01])
    framed = bytearray(append_crc(frame))
    framed[0] ^= 0xFF  # corrupt slave id byte
    assert verify_crc(bytes(framed)) is False


def test_verify_crc_too_short():
    assert verify_crc(b"\x01\x02") is False


# ---------------------------------------------------------------------------
# build_request - framing per function code
# ---------------------------------------------------------------------------

@pytest.fixture
def proto():
    return ModbusRTU()


def test_build_request_read_holding_registers(proto):
    req = proto.build_request(slave_id=5, function_code=FC_READ_HOLDING_REGISTERS,
                               address=100, quantity=2)
    assert req.raw_bytes[0] == 5
    assert req.raw_bytes[1] == FC_READ_HOLDING_REGISTERS
    assert verify_crc(req.raw_bytes)
    assert len(req.raw_bytes) == 8  # slave+fc+addr(2)+qty(2)+crc(2)


def test_build_request_read_input_registers(proto):
    req = proto.build_request(slave_id=1, function_code=FC_READ_INPUT_REGISTERS,
                               address=0, quantity=4)
    assert verify_crc(req.raw_bytes)


def test_build_request_read_coils(proto):
    req = proto.build_request(slave_id=2, function_code=FC_READ_COILS,
                               address=0, quantity=8)
    assert verify_crc(req.raw_bytes)


def test_build_request_read_discrete_inputs(proto):
    req = proto.build_request(slave_id=2, function_code=FC_READ_DISCRETE_INPUTS,
                               address=0, quantity=8)
    assert verify_crc(req.raw_bytes)


def test_build_request_write_single_register(proto):
    req = proto.build_request(slave_id=5, function_code=FC_WRITE_SINGLE_REGISTER,
                               address=10, values=[1234])
    assert verify_crc(req.raw_bytes)
    assert len(req.raw_bytes) == 8


def test_build_request_write_single_register_requires_one_value(proto):
    with pytest.raises(ValueError):
        proto.build_request(slave_id=5, function_code=FC_WRITE_SINGLE_REGISTER,
                             address=10, values=[1, 2])


def test_build_request_write_multiple_registers(proto):
    req = proto.build_request(slave_id=5, function_code=FC_WRITE_MULTIPLE_REGISTERS,
                               address=10, values=[111, 222, 333])
    assert verify_crc(req.raw_bytes)
    # slave+fc+addr(2)+qty(2)+bytecount(1)+ 3*2 data + crc(2)
    assert len(req.raw_bytes) == 1 + 1 + 2 + 2 + 1 + 6 + 2


def test_build_request_unsupported_function_code(proto):
    with pytest.raises(ValueError):
        proto.build_request(slave_id=1, function_code=0x99, address=0)


# ---------------------------------------------------------------------------
# parse_response - success paths
# ---------------------------------------------------------------------------

def test_parse_response_read_holding_registers_success(proto):
    req = proto.build_request(slave_id=5, function_code=FC_READ_HOLDING_REGISTERS,
                               address=100, quantity=2)
    # Fake a slave reply: slave 5, fc 3, byte_count 4, two registers 0x0064, 0x00C8
    body = bytes([5, FC_READ_HOLDING_REGISTERS, 4, 0x00, 0x64, 0x00, 0xC8])
    reply = append_crc(body)

    resp = proto.parse_response(req, reply)
    assert resp.success is True
    assert resp.registers == [100, 200]


def test_parse_response_read_coils_success(proto):
    req = proto.build_request(slave_id=2, function_code=FC_READ_COILS,
                               address=0, quantity=3)
    # byte_count=1, coil byte = 0b00000101 -> coil0=1, coil1=0, coil2=1
    body = bytes([2, FC_READ_COILS, 1, 0b00000101])
    reply = append_crc(body)

    resp = proto.parse_response(req, reply)
    assert resp.success is True
    assert resp.registers == [1, 0, 1]


def test_parse_response_write_single_register_success(proto):
    req = proto.build_request(slave_id=5, function_code=FC_WRITE_SINGLE_REGISTER,
                               address=10, values=[1234])
    # Modbus write-single-register replies echo the request exactly
    resp = proto.parse_response(req, req.raw_bytes)
    assert resp.success is True


def test_parse_response_write_multiple_registers_success(proto):
    req = proto.build_request(slave_id=5, function_code=FC_WRITE_MULTIPLE_REGISTERS,
                               address=10, values=[1, 2, 3])
    # Reply to FC16 is slave+fc+addr(2)+qty(2)+crc
    body = bytes([5, FC_WRITE_MULTIPLE_REGISTERS, 0, 10, 0, 3])
    reply = append_crc(body)
    resp = proto.parse_response(req, reply)
    assert resp.success is True


# ---------------------------------------------------------------------------
# parse_response - failure paths (feed the RETRY branch of the state machine)
# ---------------------------------------------------------------------------

def test_parse_response_crc_mismatch(proto):
    req = proto.build_request(slave_id=5, function_code=FC_READ_HOLDING_REGISTERS,
                               address=100, quantity=1)
    body = bytes([5, FC_READ_HOLDING_REGISTERS, 2, 0x00, 0x01])
    reply = bytearray(append_crc(body))
    reply[-1] ^= 0xFF  # corrupt CRC
    resp = proto.parse_response(req, bytes(reply))
    assert resp.success is False
    assert resp.error == "crc_mismatch"


def test_parse_response_exception_reply(proto):
    req = proto.build_request(slave_id=5, function_code=FC_READ_HOLDING_REGISTERS,
                               address=100, quantity=1)
    # Exception reply: fc | 0x80, followed by exception code (02 = illegal addr)
    body = bytes([5, FC_READ_HOLDING_REGISTERS | 0x80, 0x02])
    reply = append_crc(body)
    resp = proto.parse_response(req, reply)
    assert resp.success is False
    assert resp.error == "exception:2"


def test_parse_response_malformed_too_short(proto):
    req = proto.build_request(slave_id=5, function_code=FC_READ_HOLDING_REGISTERS,
                               address=100, quantity=1)
    resp = proto.parse_response(req, b"\x05\x03")
    assert resp.success is False
    assert resp.error == "malformed"


def test_parse_response_wrong_slave_id(proto):
    req = proto.build_request(slave_id=5, function_code=FC_READ_HOLDING_REGISTERS,
                               address=100, quantity=1)
    body = bytes([9, FC_READ_HOLDING_REGISTERS, 2, 0x00, 0x01])  # wrong slave id (9 != 5)
    reply = append_crc(body)
    resp = proto.parse_response(req, reply)
    assert resp.success is False
    assert resp.error == "malformed"


def test_parse_response_empty_bytes_is_malformed(proto):
    req = proto.build_request(slave_id=5, function_code=FC_READ_HOLDING_REGISTERS,
                               address=100, quantity=1)
    resp = proto.parse_response(req, b"")
    assert resp.success is False
    assert resp.error == "malformed"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
