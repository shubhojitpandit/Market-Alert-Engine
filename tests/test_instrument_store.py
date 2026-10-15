"""Offline tests for local provider-neutral instrument persistence."""

import json

import pytest

from data.instrument_store import InstrumentStore, InstrumentStoreError
from models.instrument import Instrument

FAKE_TOKEN = "FAKE_UPSTOX_ACCESS_TOKEN_MUST_NOT_BE_SAVED"

RELIANCE = Instrument(
    instrument_key="NSE_EQ|INE002A01018",
    name="RELIANCE INDUSTRIES LTD",
    trading_symbol="RELIANCE",
    exchange="NSE",
    segment="NSE_EQ",
    instrument_type="EQ",
)
NIFTY = Instrument(
    instrument_key="NSE_INDEX|Nifty 50",
    name="Nifty 50",
    trading_symbol="Nifty 50",
    exchange="NSE",
    segment="NSE_INDEX",
    instrument_type="INDEX",
)


def test_add_list_and_remove_saved_instruments(tmp_path) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")

    assert store.add(RELIANCE) == RELIANCE
    assert store.list_instruments() == [RELIANCE]
    assert store.contains(RELIANCE.instrument_key)
    assert store.get(RELIANCE.instrument_key) == RELIANCE

    store.remove(RELIANCE.instrument_key)

    assert store.list_instruments() == []
    assert not store.contains(RELIANCE.instrument_key)


def test_duplicate_instrument_key_is_not_added_twice(tmp_path) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")
    store.add(RELIANCE)
    duplicate = Instrument(
        instrument_key="NSE_EQ|INE002A01018",
        name="Different Name",
        trading_symbol="RELIANCE",
        exchange="NSE",
        segment="NSE_EQ",
        instrument_type="EQ",
    )

    saved = store.add(duplicate)

    assert saved == RELIANCE
    assert store.list_instruments() == [RELIANCE]


def test_instrument_store_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "instruments.json"
    first = InstrumentStore(path)
    first.add(RELIANCE)
    first.add(NIFTY)

    second = InstrumentStore(path)

    assert second.list_instruments() == [RELIANCE, NIFTY]


def test_remove_does_not_affect_other_saved_instruments(tmp_path) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")
    store.add(RELIANCE)
    store.add(NIFTY)

    store.remove(RELIANCE.instrument_key)

    assert store.list_instruments() == [NIFTY]


def test_token_is_never_persisted_by_instrument_store(tmp_path) -> None:
    path = tmp_path / "instruments.json"
    store = InstrumentStore(path)
    store.add(RELIANCE)
    payload = path.read_text(encoding="utf-8")

    assert FAKE_TOKEN not in payload
    assert "Bearer" not in payload
    parsed = json.loads(payload)
    assert "credential" not in parsed
    assert "token" not in parsed
    assert "access_token" not in parsed
    for item in parsed["instruments"]:
        assert "credential" not in item
        assert "token" not in item


def test_corrupted_json_raises_instrument_store_error(tmp_path) -> None:
    path = tmp_path / "instruments.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(InstrumentStoreError, match="Could not read"):
        InstrumentStore(path)


def test_invalid_instrument_document_is_rejected(tmp_path) -> None:
    path = tmp_path / "instruments.json"
    path.write_text(json.dumps([asdict_like()]), encoding="utf-8")

    with pytest.raises(InstrumentStoreError, match="invalid"):
        InstrumentStore(path)


def asdict_like() -> dict[str, str]:
    return {"instrument_key": "NSE_EQ|INE002A01018"}


def test_credential_fields_in_saved_file_are_rejected(tmp_path) -> None:
    path = tmp_path / "instruments.json"
    path.write_text(
        json.dumps(
            {
                "instruments": [asdict_like()],
                "token": FAKE_TOKEN,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InstrumentStoreError, match="invalid"):
        InstrumentStore(path)


def test_remove_missing_instrument_raises(tmp_path) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")
    with pytest.raises(InstrumentStoreError, match="not found"):
        store.remove("NSE_EQ|MISSING")
    with pytest.raises(InstrumentStoreError, match="not found"):
        store.get("NSE_EQ|MISSING")


def test_default_path_uses_local_app_data(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = InstrumentStore.default_path()
    assert path == tmp_path / "MarketAlertEngine" / "instruments.json"
    assert path.name == "instruments.json"
