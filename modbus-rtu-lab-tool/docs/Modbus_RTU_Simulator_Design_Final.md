# Modbus RTU Lab Simulator / Monitor — Final Design Document

## 1. Purpose

A tool to help lab engineers **test and monitor real Modbus RTU slave hardware** connected to a Raspberry Pi station, replacing manual SSH file-editing and blind polling with a controlled, validated, real-time workflow — without changing how the Pi is physically wired or accessed.

This is not a protocol simulator that fakes data — it talks to **real slaves over real serial ports**. "Simulator" here refers to the test/monitor tool used to exercise and validate real hardware under different wiring/configuration scenarios.

---

## 2. Scenarios Being Supported

| Scenario | Description | Key challenge |
|---|---|---|
| **1** | Multiple slaves on one port, same fixed baud rate | Simple round-robin polling |
| **2** | Multiple slaves on one port, **different baud rates each** | Must reconfigure port baud between transactions — the hard scheduling case |
| **3** | Multiple ports, each with its own fixed baud rate | Multiple independent polling loops, one per port |

All three are handled by the **same architecture** — Scenario 1/3 is simply the case where consecutive queued requests happen to share a baud rate, so no reconfiguration step is needed. Because of this, the engine is built to handle all three from the start rather than treating Scenario 2 as a later add-on.

---

## 3. High-Level Architecture

```
 ┌────────────────────────────┐        ┌──────────────────────────────────┐
 │   PC — PyQt6 Desktop App    │  LAN   │   Raspberry Pi — Station Engine   │
 │                              │◄──────►│                                    │
 │  - Live monitor (per port)  │  local │  - Backend service (socket)       │
 │  - Manual test panel        │  socket│  - Request Queue                  │
 │  - Config editor            │  conn. │  - Priority Scheduler             │
 │  - Diagnostics / history    │        │  - Centralized Modbus Manager     │
 │                              │        │    (non-blocking state machine)   │
 │                              │        │  - Protocol Handler (Modbus RTU)  │
 │                              │        │  - Serial Port Manager            │
 │                              │        │  - Config loader + validator      │
 │                              │        │  - Logger                         │
 └────────────────────────────┘        └───────────────┬────────────────────┘
                                                          │ RS485 (USB adapters)
                                                          ▼
                                          ┌───────────────────────────────┐
                                          │  Real Modbus RTU Slave Devices │
                                          │  (energy meters, HVAC ctrl,    │
                                          │   sensors, etc.)               │
                                          └───────────────────────────────┘
```

- **PC never touches serial hardware directly** — only talks to the Pi's backend service.
- **Pi never renders UI** — headless background service, stays lightweight, doesn't compete with polling timing for CPU.
- **SSH remains** the admin/deployment channel; the config JSON is also still hand-editable there, exactly as it is today.
- Config file is **plain JSON on disk, single shared file**, editable through the PC app (validated) or by hand over SSH.
- Must run acceptably on **either a Pi 3B or a Pi 4** — whichever is free in the lab at the time — so the whole engine is designed to be resource-light rather than assuming Pi 4 headroom.

---

## 4. Pi-Side Engine — Authoritative Architecture

This is the combined architecture confirmed against the lab's own "Proposed Modbus RTU Software Solution" report — four complementary mechanisms layered together, not alternatives:

1. **Sequential Polling** — the basic mechanism for talking to multiple slaves on one shared bus at all
2. **Request Queue + Centralized Manager** — the only way multiple software modules can share one UART safely, without conflicts
3. **Priority Scheduler** — keeps scan time reasonable as device count grows, by giving critical devices more frequent access
4. **Non-blocking State Machine** — avoids blocking the CPU while waiting for a slave's reply, which is what keeps this viable on a Pi 3B

### 4.1 Centralized Modbus Manager
- The **only** module permitted to touch the UART/serial port — no other component (scheduler, backend service, config reload logic) accesses it directly
- Owns the current port state (which port is open, current baud/parity/stopbits/databits)
- Processes exactly one request at a time, per its state machine (below)

### 4.2 Request Queue
- All work — scheduled polls **and** manual test requests from the PC app — enters as a request in a queue, never as a direct call into the manager
- Decouples "what needs to happen" from "when it actually happens on the wire," which is what makes priority and manual preemption possible

### 4.3 Priority Scheduler
- Decides which queued request the manager services next
- Critical slaves get **shorter polling intervals** (more frequent turns in the queue), not just equal round-robin treatment
- **Manual test requests from the PC app get top priority** — they preempt the next scheduled poll rather than waiting for a full rotation
- Runs independently per port, so a problem on one port doesn't stall others

### 4.4 Non-blocking State Machine (per port)

```
   ┌──────┐
   │ IDLE │◄────────────────────────────────────────┐
   └──┬───┘                                          │
      │ next request pulled from queue                │
      ▼                                                │
 ┌─────────────────┐   baud/parity differs from        │
 │ RECONFIGURE PORT │◄─ current port setting            │
 │  (if needed)     │                                   │
 └────────┬─────────┘                                   │
          ▼                                              │
   ┌─────────────┐                                       │
   │ SEND REQUEST │                                      │
   └──────┬───────┘                                      │
          ▼                                              │
   ┌───────────────┐   timeout/no response                │
   │ WAIT RESPONSE  ├──────────────┐                       │
   └──────┬─────────┘              │                       │
          │ response received       ▼                       │
          ▼                  ┌────────────┐                 │
   ┌──────────────┐          │  RETRY?     │                │
   │ PROCESS DATA  │         │ (attempt    │                │
   │ (CRC check,   │         │  < max)     │                │
   │  decode value)│         └──┬───────┬──┘                │
   └──────┬────────┘            │yes    │no                 │
          │                      ▼       ▼                   │
          │              (back to SEND) MARK SLAVE OFF        │
          ▼                                    │              │
   ┌─────────────┐                             │              │
   │ NEXT DEVICE  │◄────────────────────────────┘              │
   └──────┬───────┘                                            │
          └────────────────────────────────────────────────────┘
```

- **RECONFIGURE PORT** is inserted only when the next queued request needs different serial settings than the port is currently on — this is the concrete answer to Scenario 2 (mixed baud rates on one port). If consecutive requests share settings, this step is skipped, so Scenario 1/3 naturally costs nothing extra.
- **Retry logic**: on a timeout in WAIT RESPONSE, the manager retries the same request up to a configured maximum attempts before giving up — not a single-miss policy. Only after exhausting retries is the slave marked off.
- **Running Status is purely data-driven**: a slave is "on" only while it is the one currently returning real data; the moment retries are exhausted with no response, it goes "off." No separate paused/disabled state layer — status reflects actual data flow, nothing more.
- Because this is non-blocking, the manager never sits stalled waiting on one slow or dead device — it returns control and the processor is free, which is what keeps this workable on the more limited Pi 3B.

### 4.5 Protocol Handler (Modbus RTU)
- Builds requests / parses responses per Modbus RTU framing, including CRC16 generation and verification
- Supports the function codes needed for testing: read holding/input registers (03/04), write single/multiple registers (06/16), and read coils/discrete inputs where sensor scenarios need them
- Kept as a strictly separate module behind a simple interface (*build request for a task → parse its response*) — see Section 9 for why

### 4.6 Data Decoding Layer
- Applies data format (`int16`, `uint16`, `int32`, `uint32`, `float32`, etc.) and multiplier per register
- Handles all four 32-bit word/byte order combinations (big/little-endian × word-swapped or not), set **per register** since mixed vendor hardware won't agree on one convention
- Outputs both raw and decoded/human-readable values

### 4.7 Config Loader & Validator
- Loads the single shared JSON config on startup and whenever the PC app pushes an update
- Validates before applying: no duplicate slave IDs on a port, valid serial parameter combinations, valid register definitions
- Supports safe hot-reload: pause the affected port's manager, apply, resume
- Also detects and re-validates changes made by hand over SSH, since that path continues to work unchanged

### 4.8 Backend Service (PC ↔ Pi channel)
- Lightweight local **TCP socket service** — deliberately not a web server/browser-facing app, chosen for minimal dependency weight on the Pi (important given the Pi 3B constraint)
- Exposes: live poll results, manual test submission, config read/validated write, per-port status/health, log/history query
- The lab WiFi is treated as trusted, so no additional authentication layer is added on top of this connection

### 4.9 Logging
- Every transaction logged: timestamp, slave, port, baud, request, response or error type (timeout/CRC/malformed), retry count, response time
- Log storage is **bounded/rotated**, not left to grow unbounded, given constrained storage on the Pi 3B in particular

---

## 5. PC-Side PyQt6 App

### 5.1 Connection / Station View
- Enter/select a Pi's IP, connect
- Shows active ports on that station, slave counts, overall health at a glance

### 5.2 Live Monitor
One row per slave, grouped by port:

| Column | Source |
|---|---|
| Slave ID | config |
| Slave Type | config |
| Port | config |
| Baud Rate | config (fixed or per-slave depending on scenario) |
| Unit IP | which Pi station |
| Running Status | live — on (currently getting data) / off (retries exhausted, no data) |
| Live Value(s) | decoded per register, respecting data format + endianness |
| Last Response Time | live |

Color-coded for at-a-glance health — a lab-instrument feel, not a general dashboard.

### 5.3 Manual Test Panel
- Pick a slave + register/function code, send a one-off read or write
- Shows raw bytes and decoded value immediately
- Enters the Request Queue at top priority, preempting the next scheduled poll on that port

### 5.4 Config Editor
- Form-based editor over the single shared JSON config
- Add/edit/remove a slave: ID, type, protocol, port, serial params, unit IP, register list (address, type, data format, byte order, alias, multiplier)
- Client-side validation mirrors the Pi-side validator
- Review/diff step before pushing changes to the Pi
- Raw JSON remains hand-editable over SSH at any time — the app doesn't own exclusive access to the file

### 5.5 Diagnostics / History (later phase)
- Scrollable/searchable log of past polls, errors, retries, manual tests
- Exportable session summary (e.g. "slave 5, 20 min session, 3 timeouts, 2 retried successfully, avg response 45ms")

---

## 6. Config JSON Schema (single shared file)

```json
{
  "slave_id": 5,
  "slave_type": "energy_meter",
  "protocol": "modbus_rtu",
  "port": "/dev/ttyUSB0",
  "unit_ip": "192.168.1.50",
  "baudrate": 115200,
  "parity": "N",
  "stopbits": 1,
  "databits": 8,
  "poll_interval_ms": 2000,
  "priority": "normal",
  "max_retries": 2,
  "registers": [
    {
      "address": 100,
      "type": "holding",
      "data_format": "float32",
      "byte_order": "ABCD",
      "multiplier": 0.1,
      "alias": "Voltage Phase A",
      "unit": "V"
    }
  ]
}
```

Notes:
- `protocol` is included from day one for forward-compatibility (see Section 9) — today always `"modbus_rtu"`
- `byte_order` lives **per register**, to correctly handle mixed vendor hardware behind the same port
- `poll_interval_ms` and `priority` drive the Priority Scheduler (Section 4.3)
- `max_retries` drives the RETRY? branch of the state machine (Section 4.4)
- `port` + `baudrate` together are what the scheduler groups requests by — multiple slaves sharing a `port` but different `baudrate` is the Scenario 2 case, triggering RECONFIGURE PORT between them

---

## 7. Non-Functional Considerations

- **Resource-light on the Pi, tested against both Pi 3B and Pi 4**: single lightweight background process, non-blocking I/O rather than heavy threading, minimal dependencies
- **Bounded logging**: rotated/capped, not unbounded growth
- **Timing predictability**: RECONFIGURE PORT and retry costs are accounted for, so polling cycle timing stays meaningful for diagnostics rather than appearing to randomly slow down
- **Resilience**: a hung/misbehaving slave on one port doesn't block other ports; a malformed config change is validated before being applied, never applied blindly
- **Coexistence with existing habits**: hand-editing the JSON over SSH continues to work exactly as today

---

## 8. Development & Testing Approach

- Development and validation happen against **real hardware** in the lab (confirmed available), not just simulated/mocked slaves — so behavior claims are verified against actual devices, not assumptions
- Given the two possible host boards, testing should cover both a Pi 3B and a Pi 4 run, particularly for timing behavior under load (many slaves, mixed baud rates)

---

## 9. Multi-Protocol Extensibility (future upgrade path — not built in v1)

Modbus RTU is the only protocol in scope today, but the design avoids hard-coding it into parts of the system that shouldn't care what protocol is running.

### Already protocol-agnostic (no changes needed)
- Serial/Transport Manager, Request Queue, Priority Scheduler, Data Decoding Layer, Backend Service, Config Loader, Logger, and the PyQt6 App — all operate on generic "requests," "tasks," and "slaves," not on Modbus RTU specifics

### Stays protocol-specific, and isolated
- **Protocol Handler** — the only genuinely Modbus-RTU-specific logic (CRC16 framing, function codes) lives in its own module behind a fixed interface (*build request → parse response*). A future protocol (Modbus TCP, BACnet, DNP3, etc.) is a new module against the same interface — nothing else changes.
- **Transport vs. Protocol distinction** — RS485/serial is a transport; Modbus RTU is a protocol on top of it. Future protocols may use Ethernet/WiFi transports instead; the transport layer is treated as one of possibly several, even though only serial is built today.
- **Config schema** — the `"protocol"` field (Section 6) is already present, so adding a new protocol later means adding a new value and handler, not migrating every existing config.
- **PC app protocol-aware UI stays small and contained** — e.g. Modbus RTU needs baud/parity fields; Modbus TCP would need IP/port instead. Isolated to a small per-protocol form section, not baked into the app's overall structure.

### Net effect
No extra build work today — just two deliberate choices: the protocol handler is its own separated module, and the config schema includes `"protocol"` from day one.

---

## 10. Decisions Confirmed

1. **Running Status** — purely data-driven: "on" only while actively returning real data; "off" once retries are exhausted with no response. No separate engine-state layer.
2. **Priority queue** — per-slave polling frequency is in scope for v1 (via `poll_interval_ms` / `priority`), not deferred; manual requests get top priority and preempt scheduled polling.
3. **Retry policy** — timeouts trigger a retry up to a configured `max_retries` before marking a slave off; not a single-miss policy.
4. **Backend transport** — plain local TCP socket service, chosen for minimal dependency weight on constrained hardware (Pi 3B).
5. **v1 scope** — all three scenarios supported from the start, since the request queue + state machine architecture handles Scenario 1/3 as a simplified case of the same mechanism used for Scenario 2 (RECONFIGURE PORT step simply gets skipped when unnecessary).
6. **Security** — lab WiFi treated as trusted; no additional authentication layer added to the PC↔Pi socket connection at this stage.
