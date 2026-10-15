"""Search Upstox instruments and return provider-neutral Instrument objects."""

from __future__ import annotations

import json
import re
from ctypes import byref, create_string_buffer, get_last_error, sizeof
from ctypes import wintypes
from typing import Callable
from urllib.parse import urlencode, urlparse

from data.credential_store import CredentialStore, CredentialStoreError
from data.upstox_feed import (
    UPSTOX_USER_AGENT,
    _INTERNET_DEFAULT_HTTPS_PORT,
    _WINHTTP_ACCESS_TYPE_NO_PROXY,
    _WINHTTP_ADDREQ_FLAG_ADD,
    _WINHTTP_FLAG_SECURE,
    _WINHTTP_HEADER_LENGTH_NULL_TERMINATED,
    _WINHTTP_QUERY_FLAG_NUMBER,
    _WINHTTP_QUERY_STATUS_CODE,
    _close_winhttp_handles,
    _load_winhttp,
)
from models.instrument import Instrument

UPSTOX_INSTRUMENT_SEARCH_URL = "https://api.upstox.com/v2/instruments/search"
SEARCH_RECORD_LIMIT = 10
_MAX_QUERY_LENGTH = 50
_SEARCH_BODY_LIMIT = 65_536
_SAFE_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")


class UpstoxInstrumentSearchError(RuntimeError):
    """Raised when Upstox instrument search cannot be completed."""


def _winhttp_search_get(
    url: str,
    credential: str,
    *,
    timeout: float,
    user_agent: str,
    body_limit: int = _SEARCH_BODY_LIMIT,
) -> tuple[int, bytes]:
    """GET an Upstox instrument-search URL through WinHTTP/Schannel."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise OSError("instrument search request is not HTTPS")

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    port = parsed.port or _INTERNET_DEFAULT_HTTPS_PORT
    timeout_ms = max(1, int(timeout * 1000))
    headers = (
        "Authorization: Bearer "
        + credential
        + "\r\nAccept: application/json\r\n"
    )

    dll = _load_winhttp()
    session = connect = request = None
    try:
        session = dll.WinHttpOpen(
            user_agent,
            _WINHTTP_ACCESS_TYPE_NO_PROXY,
            None,
            None,
            0,
        )
        if not session:
            raise OSError(get_last_error())
        if not dll.WinHttpSetTimeouts(
            session, timeout_ms, timeout_ms, timeout_ms, timeout_ms
        ):
            raise OSError(get_last_error())
        connect = dll.WinHttpConnect(session, parsed.hostname, port, 0)
        if not connect:
            raise OSError(get_last_error())
        request = dll.WinHttpOpenRequest(
            connect,
            "GET",
            path,
            None,
            None,
            None,
            _WINHTTP_FLAG_SECURE,
        )
        if not request:
            raise OSError(get_last_error())
        if not dll.WinHttpAddRequestHeaders(
            request,
            headers,
            _WINHTTP_HEADER_LENGTH_NULL_TERMINATED,
            _WINHTTP_ADDREQ_FLAG_ADD,
        ):
            raise OSError(get_last_error())
        if not dll.WinHttpSendRequest(request, None, 0, None, 0, 0, 0):
            raise OSError(get_last_error())
        if not dll.WinHttpReceiveResponse(request, None):
            raise OSError(get_last_error())

        status = wintypes.DWORD(0)
        status_size = wintypes.DWORD(sizeof(status))
        if not dll.WinHttpQueryHeaders(
            request,
            _WINHTTP_QUERY_STATUS_CODE | _WINHTTP_QUERY_FLAG_NUMBER,
            None,
            byref(status),
            byref(status_size),
            None,
        ):
            raise OSError(get_last_error())

        chunks: list[bytes] = []
        remaining = body_limit
        while remaining > 0:
            buf = create_string_buffer(min(4096, remaining))
            got = wintypes.DWORD(0)
            if not dll.WinHttpReadData(request, buf, len(buf), byref(got)):
                raise OSError(get_last_error())
            if got.value == 0:
                break
            chunks.append(buf.raw[: got.value])
            remaining -= got.value
        return int(status.value), b"".join(chunks)[:body_limit]
    finally:
        headers = None
        try:
            _close_winhttp_handles(
                dll.WinHttpCloseHandle, request, connect, session
            )
        except Exception:
            pass


class UpstoxInstrumentResolver:
    """Resolve Upstox search queries to application Instrument objects."""

    def __init__(
        self,
        credential_store: CredentialStore,
        credential_id: str,
        *,
        provider: str = "upstox",
        timeout: float = 10.0,
        record_limit: int = SEARCH_RECORD_LIMIT,
        http_get: Callable[..., tuple[int, bytes]] | None = None,
    ) -> None:
        if not credential_id.strip():
            raise ValueError("credential_id must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not 1 <= record_limit <= 30:
            raise ValueError("record_limit must be between 1 and 30")
        self._credential_store = credential_store
        self._credential_id = credential_id.strip()
        self._provider = provider
        self._timeout = timeout
        self._record_limit = record_limit
        self._http_get = http_get or _winhttp_search_get

    def search(
        self,
        query: str,
        *,
        exchanges: str | None = None,
        segments: str | None = None,
        instrument_types: str | None = None,
    ) -> list[Instrument]:
        """Search Upstox instruments. Matching is performed by the provider API."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search query must not be empty")
        if len(normalized_query) > _MAX_QUERY_LENGTH:
            raise ValueError("search query is too long")

        try:
            credential = self._credential_store.retrieve(
                self._provider, self._credential_id
            )
        except CredentialStoreError:
            raise UpstoxInstrumentSearchError(
                "Upstox instrument search failed: credential error"
            ) from None
        except Exception:
            raise UpstoxInstrumentSearchError(
                "Upstox instrument search failed: credential error"
            ) from None

        parameters = {
            "query": normalized_query,
            "records": str(self._record_limit),
            "page_number": "1",
        }
        if exchanges and exchanges.strip():
            parameters["exchanges"] = exchanges.strip()
        if segments and segments.strip():
            parameters["segments"] = segments.strip()
        if instrument_types and instrument_types.strip():
            parameters["instrument_types"] = instrument_types.strip()
        url = f"{UPSTOX_INSTRUMENT_SEARCH_URL}?{urlencode(parameters)}"

        try:
            status_code, payload = self._http_get(
                url,
                credential,
                timeout=self._timeout,
                user_agent=UPSTOX_USER_AGENT,
            )
        except Exception:
            raise UpstoxInstrumentSearchError(
                "Upstox instrument search failed: network error"
            ) from None
        finally:
            credential = None

        if not isinstance(status_code, int) or not (100 <= status_code <= 599):
            raise UpstoxInstrumentSearchError(
                "Upstox instrument search failed: network error"
            )
        if status_code != 200:
            raise UpstoxInstrumentSearchError(
                self._http_error_message(status_code, payload)
            )
        return self._parse_instruments(payload)

    def _http_error_message(self, status_code: int, payload: bytes) -> str:
        message = f"Upstox instrument search failed: HTTP {status_code}"
        if status_code not in (401, 403):
            return message
        try:
            body = json.loads(payload)
        except Exception:
            return message
        code = None
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                code = errors[0].get("errorCode") or errors[0].get("error_code")
            elif isinstance(body.get("error_code"), str):
                code = body.get("error_code")
        if isinstance(code, str) and _SAFE_ERROR_CODE.fullmatch(code):
            return f"{message}; code={code}"
        return message

    @staticmethod
    def _parse_instruments(payload: bytes) -> list[Instrument]:
        try:
            body = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise UpstoxInstrumentSearchError(
                "Upstox returned an invalid instrument search response"
            ) from None
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise UpstoxInstrumentSearchError(
                "Upstox returned an invalid instrument search response"
            )

        instruments: list[Instrument] = []
        seen: set[str] = set()
        for item in body["data"]:
            if not isinstance(item, dict):
                raise UpstoxInstrumentSearchError(
                    "Upstox returned an invalid instrument search response"
                )
            try:
                instrument = Instrument(
                    instrument_key=str(item.get("instrument_key", "")),
                    name=str(item.get("name", "")),
                    trading_symbol=str(
                        item.get("trading_symbol")
                        or item.get("tradingsymbol")
                        or ""
                    ),
                    exchange=str(item.get("exchange", "")),
                    segment=str(item.get("segment", "")),
                    instrument_type=str(item.get("instrument_type", "")),
                )
            except ValueError:
                raise UpstoxInstrumentSearchError(
                    "Upstox returned an invalid instrument search response"
                ) from None
            if instrument.instrument_key in seen:
                continue
            seen.add(instrument.instrument_key)
            instruments.append(instrument)
        return instruments
