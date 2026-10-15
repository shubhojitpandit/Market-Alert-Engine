"""Offline tests for Windows-protected provider credential storage."""

import pytest

from data.credential_store import (
    CredentialStoreError,
    WindowsCredentialStore,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


def test_credential_store_saves_retrieves_and_deletes_fake_token() -> None:
    keyring = FakeKeyring()
    store = WindowsCredentialStore(backend=keyring)

    store.save("upstox", "connection-1", "FAKE_ACCESS_TOKEN_123")

    assert store.retrieve("upstox", "connection-1") == "FAKE_ACCESS_TOKEN_123"
    assert keyring.values == {
        ("MarketAlertEngine:upstox", "connection-1"):
            "FAKE_ACCESS_TOKEN_123"
    }
    store.delete("upstox", "connection-1")
    assert keyring.values == {}


def test_credential_store_isolates_providers() -> None:
    keyring = FakeKeyring()
    store = WindowsCredentialStore(backend=keyring)
    store.save("upstox", "connection-1", "FAKE_UPSTOX_TOKEN")
    store.save("binance", "connection-1", "FAKE_BINANCE_TOKEN")

    assert store.retrieve("upstox", "connection-1") == "FAKE_UPSTOX_TOKEN"
    assert store.retrieve("binance", "connection-1") == "FAKE_BINANCE_TOKEN"
    assert len(keyring.values) == 2


def test_credential_store_does_not_echo_backend_errors() -> None:
    fake_token = "FAKE_ACCESS_TOKEN_DO_NOT_ECHO"

    class FailingKeyring(FakeKeyring):
        def set_password(
            self, service: str, username: str, password: str
        ) -> None:
            raise RuntimeError(f"backend failed: {password}")

    store = WindowsCredentialStore(backend=FailingKeyring())
    with pytest.raises(CredentialStoreError) as error:
        store.save("upstox", "connection-1", fake_token)

    assert fake_token not in str(error.value)


def test_credential_store_rejects_missing_saved_credentials() -> None:
    store = WindowsCredentialStore(backend=FakeKeyring())

    with pytest.raises(CredentialStoreError, match="No stored credential"):
        store.retrieve("upstox", "missing")
