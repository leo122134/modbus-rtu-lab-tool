# Modbus RTU Lab Tool

Two-part lab tool for testing real Modbus RTU slave devices connected to a
Raspberry Pi (3B or 4) over RS485. See `docs/` for the full design doc and
master build prompt.

## Build status

| Stage | Status | Files |
|---|---|---|
| **1. Protocol layer** | ✅ Done, 21/21 tests passing | `pi_engine/protocol/base_protocol.py`, `pi_engine/protocol/modbus_rtu.py`, `tests/test_protocol.py` |
| 2. Mock slave + transport | ⬜ Not started | `tests/mock_slave.py`, `pi_engine/transport/serial_manager.py` |
| 3. State machine + decoding | ⬜ Not started | `pi_engine/engine/state_machine.py`, `pi_engine/decoding/data_decoder.py` |
| 4. Queue + scheduler | ⬜ Not started | `pi_engine/engine/request_queue.py`, `pi_engine/engine/scheduler.py` |
| 5. Config + backend service | ⬜ Not started | `pi_engine/config/`, `pi_engine/backend/socket_service.py`, `pi_engine/logger/transaction_logger.py` |
| 6. Real hardware pass | ⬜ Not started | `pi_engine/main.py` |
| 7. PC app | ⬜ Not started | everything in `pc_app/` |

The empty folders below reflect the intended structure for each future stage
(per `docs/Modbus_RTU_Project_Prompt_and_Structure.md`, Section 2) so you can
drop files straight into the right place as each stage is built.

## Run the Stage 1 tests

```bash
pip install pytest pyserial
cd modbus-rtu-lab-tool
python3 -m pytest tests/test_protocol.py -v
```

## Layout

```
modbus-rtu-lab-tool/
├── docs/                  # design doc + master build prompt
├── pi_engine/             # runs on the Raspberry Pi
│   ├── protocol/          # ✅ built - CRC16, RTU framing, build/parse interface
│   ├── transport/         # serial port open/close/reconfigure
│   ├── engine/            # request queue, scheduler, state machine
│   ├── decoding/          # data type / multiplier / byte-order per register
│   ├── config/            # JSON load/validate/hot-reload
│   ├── backend/           # TCP socket service for the PC app
│   └── logger/            # per-transaction, bounded/rotated logging
├── pc_app/                # PyQt6 desktop app (runs on the engineer's laptop)
│   ├── client/             # socket client to pi_engine backend
│   ├── views/              # connection, live monitor, manual test, config editor, diagnostics
│   └── resources/          # icons, stylesheets
└── tests/                 # ✅ test_protocol.py built; mock_slave.py + rest pending
```
