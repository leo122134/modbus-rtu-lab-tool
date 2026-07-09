# Modbus RTU Lab Tool — Master Project Prompt & Build Structure

This document is the single source of truth to hand to a new session (or Claude Code) when building any stage of this project. Paste the relevant section(s) in along with the referenced design doc.

---

## 1. Master Project Prompt

Use this as the opening context in any new build session:

```
I'm building a two-part lab tool for testing real Modbus RTU slave devices
connected to a Raspberry Pi (3B or 4) over RS485.

PI SIDE (engine, headless, resource-light — must run on Pi 3B or Pi 4):
- Owns all serial ports. Single Centralized Modbus Manager is the only module
  allowed to touch the UART.
- Application requests (scheduled polls + manual test requests) go into a
  Request Queue, never called directly on the port.
- A Priority Scheduler picks the next request: critical slaves poll more
  frequently (poll_interval_ms/priority in config); manual test requests
  from the PC app preempt scheduled polling.
- The manager processes one request at a time through a non-blocking state
  machine: IDLE -> RECONFIGURE PORT (only if baud/parity differs from
  current) -> SEND REQUEST -> WAIT RESPONSE -> (timeout -> RETRY up to
  max_retries, else PROCESS DATA) -> NEXT DEVICE -> IDLE.
- A slave's "running status" is purely data-driven: on only while it's
  currently returning real data; off once retries are exhausted.
- Protocol logic (Modbus RTU framing + CRC16) is isolated in its own module
  behind a build_request/parse_response interface, so other protocols can
  be added later without touching the scheduler or queue.
- Data decoding (data type, multiplier, byte/word order — set PER REGISTER
  since vendors differ) is a separate module from protocol framing.
- Single shared JSON config file (not one file per device) — read/validated
  on load and on any hot-reload, either pushed from the PC app or hand-
  edited over SSH (both must keep working).
- Exposes a lightweight local TCP socket service (no web server) for the
  PC app: live results, manual test submission, config read/validated
  write, per-port status, log/history query. Lab WiFi is trusted, no auth
  layer needed.
- Logging is per-transaction (timestamp, slave, port, baud, request,
  response/error, retry count, response time) and bounded/rotated, not
  unbounded.

PC SIDE (PyQt6 desktop app):
- Connects to the Pi's socket service — never touches serial hardware
  directly.
- Panels: Connection/Station view, Live Monitor (per-port table: slave id,
  type, port, baud, unit ip, running status, live decoded values, last
  response time), Manual Test panel (send one-off read/write, preempts
  queue), Config Editor (form-based, validated client-side, review/diff
  before push, JSON stays hand-editable over SSH too), Diagnostics/History
  (later phase — exportable session summaries).

SCENARIOS THE ENGINE MUST HANDLE (same architecture for all three):
1. Multiple slaves, one port, same baud rate.
2. Multiple slaves, one port, DIFFERENT baud rates each (triggers the
   RECONFIGURE PORT step between requests).
3. Multiple ports, each with its own fixed baud rate (parallel independent
   managers/queues per port).

CONSTRAINTS:
- Real hardware only — no faked/simulated data; "simulator" refers to the
  test tool, not synthetic slaves.
- Must be lightweight enough to run acceptably on a Pi 3B, not just a Pi 4.
- Config schema includes a "protocol" field (always "modbus_rtu" today) to
  leave room for future protocols without a schema migration.

Full design reference: see attached Modbus_RTU_Simulator_Design_Final.md
```

---

## 2. Recommended Folder / File Structure

```
modbus-rtu-lab-tool/
│
├── README.md                          # project overview, setup, how to run both sides
├── docs/
│   └── Modbus_RTU_Simulator_Design_Final.md   # the full design doc (already built)
│
├── pi_engine/                         # everything that runs ON the Raspberry Pi
│   ├── main.py                        # entrypoint — starts engine + backend service
│   ├── requirements.txt               # pyserial, etc. — kept minimal
│   │
│   ├── protocol/
│   │   ├── __init__.py
│   │   ├── base_protocol.py           # interface: build_request() / parse_response()
│   │   └── modbus_rtu.py              # CRC16, RTU framing, function codes 03/04/06/16
│   │
│   ├── transport/
│   │   ├── __init__.py
│   │   └── serial_manager.py          # open/close/reconfigure a serial port
│   │
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── request_queue.py           # thread-safe queue: scheduled + manual requests
│   │   ├── scheduler.py               # priority scheduler (per-slave interval/priority)
│   │   └── state_machine.py           # IDLE/RECONFIGURE/SEND/WAIT/RETRY/PROCESS/NEXT
│   │
│   ├── decoding/
│   │   ├── __init__.py
│   │   └── data_decoder.py            # data types, multiplier, byte/word order per reg
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── loader.py                  # load/hot-reload the shared JSON config
│   │   ├── validator.py               # schema + logical validation (dup IDs, etc.)
│   │   └── slaves_config.json         # the actual live config (sample checked in)
│   │
│   ├── backend/
│   │   ├── __init__.py
│   │   └── socket_service.py          # TCP socket service consumed by the PC app
│   │
│   └── logger/
│       ├── __init__.py
│       └── transaction_logger.py      # per-transaction logging, bounded/rotated
│
├── pc_app/                            # everything that runs on the engineer's laptop
│   ├── main.py                        # PyQt6 entrypoint
│   ├── requirements.txt               # PyQt6, etc.
│   │
│   ├── client/
│   │   ├── __init__.py
│   │   └── backend_client.py          # socket client talking to pi_engine backend
│   │
│   ├── views/
│   │   ├── __init__.py
│   │   ├── connection_view.py         # enter/select Pi IP, connect, station overview
│   │   ├── live_monitor_view.py       # per-port live table
│   │   ├── manual_test_view.py        # one-off read/write panel
│   │   ├── config_editor_view.py      # form-based config editing + review/diff
│   │   └── diagnostics_view.py        # history/log viewer (later phase)
│   │
│   └── resources/
│       └── icons, qss stylesheet, etc.
│
└── tests/
    ├── test_protocol.py                # CRC16 + framing correctness
    ├── test_state_machine.py           # state transitions incl. retry + reconfigure
    ├── test_scheduler.py               # priority ordering, manual preemption
    ├── test_decoding.py                # byte/word order + multiplier correctness
    └── mock_slave.py                   # virtual serial pair + fake slave, for testing
                                         # without needing hardware attached every time
```

**Why this shape:**
- `pi_engine/` and `pc_app/` are fully separable — could become two repos later without restructuring, even though we're building them together now
- Each Pi-side module maps 1:1 to a section in the design doc (protocol, transport, engine, decoding, config, backend, logger) — easy to build and test in isolation, in the staged order below
- `tests/mock_slave.py` lets protocol/state-machine/scheduler logic be verified even between real-hardware sessions

---

## 3. Recommended Build Order (staged sessions)

Each stage is self-contained enough to fit in one focused session and be verified before moving on:

| Stage | Files touched | Verifies |
|---|---|---|
| **1. Protocol layer** | `protocol/base_protocol.py`, `protocol/modbus_rtu.py`, `tests/test_protocol.py` | CRC16 + framing correctness, in isolation |
| **2. Mock slave + transport** | `tests/mock_slave.py`, `transport/serial_manager.py` | Real serial I/O works against a virtual/mock slave before touching real hardware |
| **3. State machine + decoding** | `engine/state_machine.py`, `decoding/data_decoder.py`, `tests/test_state_machine.py`, `tests/test_decoding.py` | Retry logic, RECONFIGURE step, byte/word order decoding |
| **4. Queue + scheduler** | `engine/request_queue.py`, `engine/scheduler.py`, `tests/test_scheduler.py` | Priority ordering + manual preemption, multi-slave mixed-baud scenario |
| **5. Config + backend service** | `config/loader.py`, `config/validator.py`, `backend/socket_service.py`, `logger/transaction_logger.py` | Whole engine controllable end-to-end via socket |
| **6. Real hardware pass** | `pi_engine/main.py` | Everything above verified against actual lab devices (Pi 3B and Pi 4) |
| **7. PC app** | everything in `pc_app/` | Built last, against an already-verified backend |

---

## 4. How to Use This Across Sessions

- Start a **new chat per stage** to keep sessions light on the free plan
- Paste **Section 1 (Master Prompt)** plus the specific stage's row from **Section 3**, and attach `Modbus_RTU_Simulator_Design_Final.md`
- Say which files already exist (paste them back in, or attach) so the new session builds on top rather than restarting
