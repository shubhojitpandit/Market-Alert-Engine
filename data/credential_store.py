"""Provider-independent interfaces and OS-protected credential storage."""

from abc import ABC, abstractmethod


class CredentialStoreError(RuntimeError):
    """Raised when a credential cannot be securely stored or retrieved."""


class CredentialStore(ABC):
    """Store credentials independently of provider and market-data adapters."""

    @abstractmethod
    def save(self, provider: str, credential_id: str, credential: str) -> None:
        """Persist a credential in the protected local credential store."""
        raise NotImplementedError

    @abstractmethod
    def retrieve(self, provider: str, credential_id: str) -> str:
        """Retrieve a credential by provider and connection identifier."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, provider: str, credential_id: str) -> None:
        """Remove a credential from the protected local credential store."""
        raise NotImplementedError


class WindowsCredentialStore(CredentialStore):
    """Use the current Windows user's Credential Locker via the keyring API."""

    _SERVICE_PREFIX = "MarketAlertEngine"

    def __init__(self, backend: object | None = None) -> None:
        from os import name

        if name != "nt":
            raise CredentialStoreError(
                "The Windows protected credential store is unavailable"
            )

        try:
            if backend is None:
                from keyring.backends.Windows import WinVaultKeyring

                backend = WinVaultKeyring()
        except Exception:
            raise CredentialStoreError(
                "Could not initialize the Windows protected credential store"
            ) from None

        self._backend = backend

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self._validate(provider, credential_id, credential)
        try:
            self._backend.set_password(
                self._service(provider), credential_id, credential
            )
        except Exception:
            raise CredentialStoreError(
                "Could not store the credential in Windows Credential Locker"
            ) from None

    def retrieve(self, provider: str, credential_id: str) -> str:
        self._validate(provider, credential_id)
        try:
            credential = self._backend.get_password(
                self._service(provider), credential_id
            )
        except Exception:
            raise CredentialStoreError(
                "Could not retrieve the credential from Windows Credential Locker"
            ) from None
        if not credential:
            raise CredentialStoreError("No stored credential was found")
        return credential

    def delete(self, provider: str, credential_id: str) -> None:
        self._validate(provider, credential_id)
        try:
            self._backend.delete_password(
                self._service(provider), credential_id
            )
        except Exception:
            raise CredentialStoreError(
                "Could not remove the credential from Windows Credential Locker"
            ) from None

    @classmethod
    def _service(cls, provider: str) -> str:
        return f"{cls._SERVICE_PREFIX}:{provider}"

    @staticmethod
    def _validate(
        provider: str, credential_id: str, credential: str | None = None
    ) -> None:
        if not provider or not credential_id:
            raise ValueError("provider and credential_id must not be empty")
        if credential is not None and not credential:
            raise ValueError("credential must not be empty")
