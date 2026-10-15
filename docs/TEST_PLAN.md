# Market Alert Engine V1 Test Plan

## 1. Purpose

Define a repeatable QA approach for the existing simulated Market Alert Engine and its documented V1 behavior.

## 2. Scope

This plan covers the configured simulated feed, the offline-testable MetaTrader 5 and Upstox adapters, provider-neutral connection management, local protected credential storage, alert model and manager, trigger evaluation, FIFO notification queue, notification handler isolation, in-memory history, integrated simulation, and interactive CLI. Live broker verification and GUI behavior remain out of automated scope.

## 3. System under test

The system under test is the current Python application in `data/`, `models/`, `engine/`, `notifications/`, `history/`, and the `main.py` CLI coordinator.

## 4. Testing levels

- **Component testing (automated):** pytest checks the simulated feed, fake-API MT5 adapter, mocked Upstox V3 adapter, fake credential store, connection manager, engine, queue, and history behavior. Provider tests do not connect to a live service or terminal.
- **Integration testing (automated):** pytest feeds deterministic `PriceUpdate` values through the engine and uses fake notification handlers to verify queue and history wiring.
- **End-to-end/manual QA:** the interactive application, actual Windows sound, and visible Windows toast are checked manually; prior records are in [TESTING_LOG.md](./TESTING_LOG.md).

## 5. Test environment

- Python 3.14 on Windows for the recorded manual checks.
- pytest for the automated component/integration suite.
- No network market data or database is required.
- Automated notification checks do not invoke sound or desktop UI.

## 6. Test data / deterministic simulation

Tests use fixed prices, fixed UTC timestamps, the existing instrument configuration, zero update interval, and fixed random seeds. Focused trigger tests construct `PriceUpdate` values directly; feed tests use reproducible seeds and custom configurations where appropriate.

## 7. Functional test areas

- Simulated instruments, positive rounded prices, update timestamps, and seed determinism.
- Credential save/retrieval/deletion, provider namespace isolation, and ensuring credentials are absent from registry files and user-visible errors.
- Upstox WinHTTP/Schannel authorization (mocked at the WinHTTP boundary), LTPC protobuf normalization, malformed and invalid feed messages, connection failures, and clean disconnect with mocked network/WebSocket behavior.
- Connection lifecycle, including disconnect preserving credentials and delete removing them.
- Alert registration, IDs, supported conditions, active retrieval, lifecycle restrictions, and validation.
- Inclusive ABOVE/BELOW boundaries, crossing boundaries, first-update behavior, multiple alerts, and instrument isolation.
- Notification queue FIFO, empty behavior, message details, and delivery failure isolation.
- History ordering, recent retrieval, duplicate prevention, triggering price, UTC timestamp, and immutability.
- CLI paths and integrated runtime are manually verified as recorded in the QA log.

## 8. Negative testing

Automated checks reject unsupported symbols in the feed, invalid alert fields and counts, invalid lifecycle transitions, non-triggered queue inserts, negative history limits, and cancelled history records. CLI manual checks include invalid choices and alert fields.

## 9. Regression testing

Run the complete pytest suite after relevant changes. Also start the application with `python main.py` and perform the applicable manual checks in [TESTING_LOG.md](./TESTING_LOG.md), especially after notification or CLI changes.

## 10. Notification testing approach

Automated tests use fake handlers or `NotificationHandler` subclasses that override channel methods. Tests assert that channel failures are reported and later queue items continue. They do not play Windows sounds or create real toasts. Actual Windows sound and toast appearance require manual QA.

## 11. Manual testing approach

Use `python main.py`, exercise the CLI and its bounded simulation, and observe actual Windows sound/toast delivery. For Upstox verification, install requirements, use **Manage Upstox Connections** → **Create connection**, enter the configured instrument key and real access token only at the hidden runtime prompt, then connect. Confirm LTPC updates reach the alert path, Disconnect stops delivery while retaining the connection for a subsequent Connect, Delete removes it, and application exit closes active sockets. Never copy a real token into a file or terminal command. These live checks require a valid local credential, network access, and an Upstox account; they are not part of pytest. For MT5, with the package installed and signed-in terminal running, consume one update from `MT5PriceFeed().stream("XAUUSDm", count=1)` and check the symbol, bid/ask midpoint, UTC timestamp, and terminal shutdown. Record expected versus actual results, status, and defects in [TESTING_LOG.md](./TESTING_LOG.md).

## 12. Known limitations

- Alert history is an in-memory V1 component.
- Upstox credentials require the Windows Credential Locker. Authorization uses Windows WinHTTP/Schannel because the Python OpenSSL HTTP path was rejected by the provider edge; the WebSocket still uses the existing `websockets` package. Live LTPC for `NSE_INDEX|Nifty 50` was verified through the CLI connect flow.
- Automated tests do not prove that a Windows toast is visible or that audio is physically audible.
- Seeded random output is reproducible for a fixed call order; changing instrument order changes the random sequence consumed.

## 13. Exit criteria

- Complete pytest suite passes with no errors.
- `python main.py` starts and exits normally.
- Current manual QA records remain available, including prior defects and retests.
- No automated test depends on external market data, timing-sensitive behavior, Windows UI, or audio hardware.
