# Market Alert Engine V1 Requirements Traceability

Automated test names below refer to pytest node IDs in `tests/`. Manual test IDs refer to [TESTING_LOG.md](./TESTING_LOG.md). “Implemented” records the current implementation and test evidence; it does not imply a coverage percentage.

| ID | Requirement | Implementation component | Automated test(s) | Manual test ID | Status |
|---|---|---|---|---|---|
| R-001 | Multi-asset price feed | `data/simulated_feed.py` | `tests/test_simulated_feed.py` | TR-001, TR-006 | Implemented |
| R-002 | Instrument selection | `main.py`, `data/simulated_feed.py` | `tests/test_simulated_feed.py::test_configured_instruments_are_available` | TR-007 | Implemented |
| R-003 | Above condition | `engine/alert_engine.py` | `tests/test_alert_engine.py::test_above_uses_inclusive_boundary` | TR-003 | Implemented |
| R-004 | Below condition | `engine/alert_engine.py` | `tests/test_alert_engine.py::test_below_uses_inclusive_boundary` | TR-003 | Implemented |
| R-005 | Cross-above condition | `engine/alert_engine.py` | `tests/test_alert_engine.py::test_cross_above_boundaries` | TR-003 | Implemented |
| R-006 | Cross-below condition | `engine/alert_engine.py` | `tests/test_alert_engine.py::test_cross_below_boundaries` | TR-003 | Implemented |
| R-007 | Multiple active alerts | `engine/alert_manager.py`, `engine/alert_engine.py` | `tests/test_alert_manager.py::test_active_and_all_alert_views_preserve_registration_order`; `tests/test_alert_engine.py::test_multiple_alerts_trigger_independently_in_registration_order` | TR-003, TR-006 | Implemented |
| R-008 | Alert lifecycle | `models/alert.py`, `engine/alert_manager.py` | `tests/test_alert_manager.py::test_cancel_and_trigger_update_status_and_leave_active_collection`; lifecycle restriction tests | TR-002 | Implemented |
| R-009 | Trigger detection | `engine/alert_engine.py` | `tests/test_alert_engine.py` | TR-003 | Implemented |
| R-010 | Notification queue | `notifications/notification_queue.py` | `tests/test_notification_queue.py` | TR-004, TR-006 | Implemented |
| R-011 | FIFO notification processing | `notifications/notification_queue.py` | `tests/test_notification_queue.py::test_enqueue_dequeue_and_process_are_fifo`; `test_process_all_delivers_every_simultaneous_notification_once` | TR-004 | Implemented |
| R-012 | Sound notification | `notifications/notification_handler.py` | `tests/test_notification_queue.py::test_handler_channel_failures_do_not_stop_queue_processing` (channel mocked) | TR-004, TR-007 | Implemented; manual sound confirmation required |
| R-013 | Windows desktop notification | `notifications/notification_handler.py` | `tests/test_notification_queue.py` (desktop channel mocked) | TR-004, TR-007 | Implemented; manual toast confirmation required |
| R-014 | Notification failure isolation | `notifications/notification_handler.py`, `notifications/notification_queue.py` | `tests/test_notification_queue.py::test_handler_channel_failures_do_not_stop_queue_processing` | TR-004 | Implemented |
| R-015 | Alert history | `history/alert_history.py` | `tests/test_alert_history.py` | TR-005 | Implemented |
| R-016 | Actual triggering price | `history/alert_history.py`, `engine/alert_engine.py` | `tests/test_alert_history.py::test_engine_records_trigger_details_and_actual_price`; `tests/test_alert_engine.py::test_actual_price_order_and_history_notification_integration` | TR-005 | Implemented |
| R-017 | Duplicate prevention | `engine/alert_engine.py`, `history/alert_history.py` | `tests/test_alert_engine.py::test_triggered_alert_does_not_trigger_again`; `tests/test_alert_history.py::test_duplicate_trigger_record_is_ignored` | TR-004, TR-005, TR-006 | Implemented |
| R-018 | Cancellation | `engine/alert_manager.py`, `engine/alert_engine.py` | `tests/test_alert_manager.py`; `tests/test_alert_engine.py::test_cancelled_alert_never_triggers`; `tests/test_alert_history.py::test_cancelled_alert_cannot_be_recorded` | TR-002, TR-003, TR-006, TR-007 | Implemented |
| R-019 | Instrument isolation | `engine/alert_engine.py` | `tests/test_alert_engine.py::test_instrument_isolation_and_previous_prices_are_independent` | TR-003, TR-006 | Implemented |
| R-020 | Interactive CLI | `main.py` | — (manual QA) | TR-007 | Implemented |
| R-021 | Simulated runtime | `main.py`, `data/simulated_feed.py` | — (manual end-to-end) | TR-006, TR-007 | Implemented |
| R-022 | Deterministic/reproducible simulation | `data/simulated_feed.py` | `tests/test_simulated_feed.py::test_same_seed_reproduces_updates_for_multiple_instruments` | TR-006 | Implemented |
| R-023 | No dependency on live market data for V1 | `data/simulated_feed.py`, `data/base_feed.py` | `tests/test_simulated_feed.py` | TR-001, TR-006 | Implemented |
| R-024 | Separation of feed from alert engine | `data/base_feed.py`, `engine/alert_engine.py` | `tests/test_alert_engine.py` uses `PriceUpdate` objects directly | TR-003, TR-006 | Implemented |
| R-025 | MT5 ticks normalize through `PriceFeed` to provider-agnostic alert evaluation | `data/mt5_feed.py`, `data/base_feed.py`, `main.py` | `tests/test_mt5_feed.py::test_mt5_feed_implements_price_feed_and_normalizes_xauusdm_tick`; `tests/test_main.py::test_live_feed_updates_flow_into_alert_engine_and_notification_queue` | — (live validation pending) | Automated integration check implemented; live terminal check pending |
| R-026 | Provider credentials are stored separately from provider adapters and protected by the Windows user credential store | `data/credential_store.py`, `data/connection_manager.py` | `tests/test_credential_store.py`; `tests/test_connection_manager.py::test_connection_metadata_persists_without_credential` | — (local Credential Locker verification pending) | Implemented; Windows manual verification pending |
| R-027 | Saved connections can be connected, disconnected without credential loss, and deleted with credential removal | `data/connection_manager.py`, `main.py` | `tests/test_connection_manager.py::test_disconnect_preserves_credential_and_reconnect_reuses_it`; `test_delete_disconnects_and_removes_saved_connection_and_credential`; `test_application_close_disconnects_without_deleting_credentials` | — (CLI manual verification pending) | Implemented; CLI manual verification pending |
| R-028 | Upstox V3 LTPC WebSocket updates normalize through `PriceFeed` to the existing alert path | `data/upstox_feed.py`, `data/connection_manager.py`, `main.py` | `tests/test_upstox_feed.py`; `tests/test_connection_manager.py` | TR-008 | Implemented. Authorize uses Windows WinHTTP/Schannel (Python urllib/OpenSSL was rejected by the provider edge). The WebSocket still uses the existing `websockets` client. Live LTPC for `NSE_INDEX\|Nifty 50` was received through `PriceUpdate`. |
