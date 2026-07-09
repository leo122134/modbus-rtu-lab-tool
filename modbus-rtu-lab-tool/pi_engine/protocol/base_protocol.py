"""
base_protocol.py

Defines the fixed interface every protocol handler must implement.

Per the design doc (Section 9 - Multi-Protocol Extensibility), the ONLY
protocol-specific logic in the whole system lives behind this interface.
The Request Queue, Scheduler, State Machine, Decoding layer, Backend
Service, Config Loader, and Logger are all protocol-agnostic and only
ever talk to a protocol handler through build_request() / parse_response().

Adding a new protocol later (Modbus TCP, BACnet, DNP3, ...) means writing
a new class against this interface - nothing else in the system changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ProtocolRequest:
    """
    A protocol-agnostic description of "what to ask a slave for".
    Built by build_request(), consumed by the transport layer as raw bytes.
    """
    slave_id: int
    function_code: int
    address: int
    quantity: int = 1              # number of registers/coils, or values to write
    values: Optional[list] = None  # for write requests
    raw_bytes: bytes = b""         # the actual wire bytes to send
    meta: dict = field(default_factory=dict)  # protocol handler can stash extra info
                                               # it needs later to parse the response
                                               # (e.g. expected byte count)


@dataclass
class ProtocolResponse:
    """
    Result of parsing a slave's raw reply.
    success=False + error set covers: timeout, CRC failure, malformed frame,
    exception code from the slave, etc. The state machine only needs to know
    success/failure + an error category; the Decoding layer only needs
    'registers' (raw register words) - it doesn't know or care about protocol
    framing.
    """
    success: bool
    slave_id: Optional[int] = None
    function_code: Optional[int] = None
    registers: Optional[list] = None   # raw 16-bit words, before data_decoder.py
                                        # applies data_format/multiplier/byte_order
    raw_bytes: bytes = b""
    error: Optional[str] = None        # e.g. "timeout", "crc_mismatch",
                                        # "malformed", "exception:<code>"


class BaseProtocol(ABC):
    """
    Fixed interface. Every concrete protocol handler (ModbusRTU today) must
    implement exactly these two methods and nothing more is assumed by the
    rest of the system.
    """

    @abstractmethod
    def build_request(
        self,
        slave_id: int,
        function_code: int,
        address: int,
        quantity: int = 1,
        values: Optional[list] = None,
    ) -> ProtocolRequest:
        """
        Build a ProtocolRequest (including raw wire bytes) for a task.
        Task originates from either a scheduled poll or a manual test
        request - build_request() doesn't know or care which.
        """
        raise NotImplementedError

    @abstractmethod
    def parse_response(
        self,
        request: ProtocolRequest,
        raw_bytes: bytes,
    ) -> ProtocolResponse:
        """
        Parse raw bytes received from the slave (after the transport layer
        collected them) into a ProtocolResponse. Must NOT raise on malformed
        input - malformed/CRC-failed data is a normal outcome and must come
        back as ProtocolResponse(success=False, error=...), since the state
        machine's RETRY branch depends on this, not on exceptions.
        """
        raise NotImplementedError
