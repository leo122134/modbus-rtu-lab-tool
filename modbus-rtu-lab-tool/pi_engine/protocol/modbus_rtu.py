"""
modbus_rtu.py

Modbus RTU protocol handler: CRC16 generation/verification, RTU framing,
and support for the function codes the design doc calls for:

  01  Read Coils
  02  Read Discrete Inputs
  03  Read Holding Registers
  04  Read Input Registers
  06  Write Single Register
  16  Write Multiple Registers   (0x10)

This is the ONLY module in the system that knows anything about Modbus RTU
specifics (CRC16, byte framing, function codes). It implements BaseProtocol
and nothing outside this file should need to change if a different protocol
is added later.
"""

import struct
from typing import Optional

from .base_protocol import BaseProtocol, ProtocolRequest, ProtocolResponse


# ---------------------------------------------------------------------------
# Function codes supported
# ---------------------------------------------------------------------------
FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_REGISTERS = 0x10

_READ_FUNCTION_CODES = {
    FC_READ_COILS,
    FC_READ_DISCRETE_INPUTS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
}
_WRITE_FUNCTION_CODES = {
    FC_WRITE_SINGLE_REGISTER,
    FC_WRITE_MULTIPLE_REGISTERS,
}

# Modbus exception response: reply function code has high bit set (fc | 0x80)
_EXCEPTION_BIT = 0x80


def crc16(data: bytes) -> int:
    """
    Standard Modbus CRC16 (poly 0xA001, init 0xFFFF), LSB-first.
    Returns the 16-bit CRC as an int (low byte, high byte order is applied
    by the caller when appending to the frame).
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(frame: bytes) -> bytes:
    """Append Modbus RTU CRC16 to a frame, low byte first."""
    crc = crc16(frame)
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def verify_crc(frame_with_crc: bytes) -> bool:
    """True if the last two bytes match the CRC16 of everything before them."""
    if len(frame_with_crc) < 3:
        return False
    body, received_crc = frame_with_crc[:-2], frame_with_crc[-2:]
    expected = crc16(body)
    expected_bytes = bytes([expected & 0xFF, (expected >> 8) & 0xFF])
    return expected_bytes == received_crc


class ModbusRTU(BaseProtocol):

    def build_request(
        self,
        slave_id: int,
        function_code: int,
        address: int,
        quantity: int = 1,
        values: Optional[list] = None,
    ) -> ProtocolRequest:
        if function_code in _READ_FUNCTION_CODES:
            body = struct.pack(">BBHH", slave_id, function_code, address, quantity)
            frame = append_crc(body)
            return ProtocolRequest(
                slave_id=slave_id,
                function_code=function_code,
                address=address,
                quantity=quantity,
                raw_bytes=frame,
                meta={"expected_reg_count": quantity},
            )

        if function_code == FC_WRITE_SINGLE_REGISTER:
            if not values or len(values) != 1:
                raise ValueError("write single register requires exactly one value")
            body = struct.pack(">BBHH", slave_id, function_code, address, values[0] & 0xFFFF)
            frame = append_crc(body)
            return ProtocolRequest(
                slave_id=slave_id,
                function_code=function_code,
                address=address,
                quantity=1,
                values=values,
                raw_bytes=frame,
            )

        if function_code == FC_WRITE_MULTIPLE_REGISTERS:
            if not values:
                raise ValueError("write multiple registers requires at least one value")
            qty = len(values)
            byte_count = qty * 2
            body = struct.pack(">BBHHB", slave_id, function_code, address, qty, byte_count)
            for v in values:
                body += struct.pack(">H", v & 0xFFFF)
            frame = append_crc(body)
            return ProtocolRequest(
                slave_id=slave_id,
                function_code=function_code,
                address=address,
                quantity=qty,
                values=values,
                raw_bytes=frame,
            )

        raise ValueError(f"unsupported function code: {function_code}")

    def parse_response(
        self,
        request: ProtocolRequest,
        raw_bytes: bytes,
    ) -> ProtocolResponse:
        # No data at all -> caller (state machine) treats this as a timeout,
        # not something parse_response needs to classify. But guard anyway.
        if not raw_bytes or len(raw_bytes) < 5:
            return ProtocolResponse(
                success=False,
                slave_id=request.slave_id,
                function_code=request.function_code,
                raw_bytes=raw_bytes,
                error="malformed",
            )

        if not verify_crc(raw_bytes):
            return ProtocolResponse(
                success=False,
                slave_id=request.slave_id,
                function_code=request.function_code,
                raw_bytes=raw_bytes,
                error="crc_mismatch",
            )

        resp_slave_id = raw_bytes[0]
        resp_fc = raw_bytes[1]

        if resp_slave_id != request.slave_id:
            return ProtocolResponse(
                success=False,
                slave_id=request.slave_id,
                function_code=request.function_code,
                raw_bytes=raw_bytes,
                error="malformed",
            )

        # Exception response: function code echoed back with high bit set,
        # followed by a single exception code byte.
        if resp_fc & _EXCEPTION_BIT:
            exception_code = raw_bytes[2] if len(raw_bytes) > 2 else None
            return ProtocolResponse(
                success=False,
                slave_id=resp_slave_id,
                function_code=resp_fc & ~_EXCEPTION_BIT,
                raw_bytes=raw_bytes,
                error=f"exception:{exception_code}",
            )

        if resp_fc != request.function_code:
            return ProtocolResponse(
                success=False,
                slave_id=resp_slave_id,
                function_code=resp_fc,
                raw_bytes=raw_bytes,
                error="malformed",
            )

        if resp_fc in _READ_FUNCTION_CODES:
            byte_count = raw_bytes[2]
            data = raw_bytes[3:3 + byte_count]
            if len(data) != byte_count:
                return ProtocolResponse(
                    success=False,
                    slave_id=resp_slave_id,
                    function_code=resp_fc,
                    raw_bytes=raw_bytes,
                    error="malformed",
                )

            if resp_fc in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS):
                # Bit-packed: unpack into a list of 0/1 per requested quantity
                bits = []
                for byte in data:
                    for bit_index in range(8):
                        bits.append((byte >> bit_index) & 0x01)
                registers = bits[: request.quantity]
            else:
                # 16-bit register words, big-endian pairs, raw (undecoded) -
                # data_decoder.py applies data_format/byte_order/multiplier
                registers = [
                    struct.unpack(">H", data[i:i + 2])[0]
                    for i in range(0, len(data), 2)
                ]

            return ProtocolResponse(
                success=True,
                slave_id=resp_slave_id,
                function_code=resp_fc,
                registers=registers,
                raw_bytes=raw_bytes,
            )

        if resp_fc in _WRITE_FUNCTION_CODES:
            # Echo-style confirmation frames for both 06 and 16 - success is
            # confirmed by CRC + matching slave/function code above.
            return ProtocolResponse(
                success=True,
                slave_id=resp_slave_id,
                function_code=resp_fc,
                registers=None,
                raw_bytes=raw_bytes,
            )

        return ProtocolResponse(
            success=False,
            slave_id=resp_slave_id,
            function_code=resp_fc,
            raw_bytes=raw_bytes,
            error="malformed",
        )
