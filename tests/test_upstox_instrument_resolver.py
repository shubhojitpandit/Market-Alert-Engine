"""Offline tests for Upstox instrument search. HTTP is mocked."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from data.credential_store import CredentialStore, CredentialStoreError
from data.upstox_instrument_resolver import (
    SEARCH_RECORD_LIMIT,
    UPSTOX_INSTRUMENT_SEARCH_URL,
    UpstoxInstrumentResolver,
    UpstoxInstrumentSearchError,
)
from models.instrument import Instrument

FAKE_TOKEN = "FAKE_UPSTOX_ACCESS_TOKEN_SEARCH_TEST"
CREDENTIAL_ID = "connection-1"

RELIANCE_PAYLOAD = {
    "status": "success",
    "data": [
        {
            "name": "RELIANCE INDUSTRIES LTD",
            "segment": "NSE_EQ",
            "exchange": "NSE",
            "instrument_key": "NSE_EQ|INE002A01018",
            "trading_symbol": "RELIANCE",
            "instrument_type": "EQ",
        },
        {
            "name": "RELIANCE INFRASTRUCTURE LTD",
            "segment": "NSE_EQ",
            "exchange": "NSE",
            "instrument_key": "NSE_EQ|INE036A01016",
            "tradingsymbol": "RELINFRA",
            "instrument_type": "EQ",
        },
    ],
}


class MemoryCredentialStore(CredentialStore):
    def __init__(self, values: dict[tuple[str, str], str] | None = None) -> None:
        self.values = values or {}

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self.values[(provider, credential_id)] = credential

    def retrieve(self, provider: str, credential_id: str) -> str:
        try:
            return self.values[(provider, credential_id)]
        except KeyError:
            raise CredentialStoreError("No stored credential was found") from None

    def delete(self, provider: str, credential_id: str) -> None:
        del self.values[(provider, credential_id)]


class RecordingHttp:
    def __init__(
        self,
        status_code: int = 200,
        payload: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = RELIANCE_PAYLOAD if payload is None else payload
        self.error = error
        self.calls: list[tuple[str, str, float, str]] = []

    def __call__(
        self,
        url: str,
        credential: str,
        *,
        timeout: float,
        user_agent: str,
    ) -> tuple[int, bytes]:
        self.calls.append((url, credential, timeout, user_agent))
        if self.error is not None:
            raise self.error
        if isinstance(self.payload, bytes):
            body = self.payload
        else:
            body = json.dumps(self.payload).encode("utf-8")
        return self.status_code, body


def make_resolver(http: RecordingHttp) -> UpstoxInstrumentResolver:
    store = MemoryCredentialStore({("upstox", CREDENTIAL_ID): FAKE_TOKEN})
    return UpstoxInstrumentResolver(
        store, CREDENTIAL_ID, http_get=http
    )


def test_successful_partial_search_returns_normalized_instruments() -> None:
    http = RecordingHttp()
    resolver = make_resolver(http)

    results = resolver.search("REL")

    assert results == [
        Instrument(
            "NSE_EQ|INE002A01018",
            "RELIANCE INDUSTRIES LTD",
            "RELIANCE",
            "NSE",
            "NSE_EQ",
            "EQ",
        ),
        Instrument(
            "NSE_EQ|INE036A01016",
            "RELIANCE INFRASTRUCTURE LTD",
            "RELINFRA",
            "NSE",
            "NSE_EQ",
            "EQ",
        ),
    ]
    url, credential, timeout, user_agent = http.calls[0]
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert url.startswith(UPSTOX_INSTRUMENT_SEARCH_URL)
    assert query["query"] == ["REL"]
    assert query["records"] == [str(SEARCH_RECORD_LIMIT)]
    assert query["page_number"] == ["1"]
    assert credential == FAKE_TOKEN
    assert timeout > 0
    assert user_agent == "MarketAlertEngine/1.0"


def test_search_sends_case_preserving_query_for_provider_matching() -> None:
    http = RecordingHttp()
    resolver = make_resolver(http)

    resolver.search("rel")
    resolver.search("REL")

    first = parse_qs(urlparse(http.calls[0][0]).query)["query"][0]
    second = parse_qs(urlparse(http.calls[1][0]).query)["query"][0]
    assert first == "rel"
    assert second == "REL"


def test_empty_or_whitespace_query_is_rejected() -> None:
    http = RecordingHttp()
    resolver = make_resolver(http)

    with pytest.raises(ValueError, match="empty"):
        resolver.search("   ")
    with pytest.raises(ValueError, match="empty"):
        resolver.search("")
    assert http.calls == []


def test_zero_result_response_returns_empty_list() -> None:
    http = RecordingHttp(payload={"status": "success", "data": []})
    resolver = make_resolver(http)

    assert resolver.search("ZZZZNOTFOUND") == []


def test_malformed_response_is_rejected() -> None:
    http = RecordingHttp(payload=b"not-json")
    resolver = make_resolver(http)

    with pytest.raises(
        UpstoxInstrumentSearchError, match="invalid instrument search"
    ) as error:
        resolver.search("REL")
    assert FAKE_TOKEN not in str(error.value)


def test_http_error_reports_status_without_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    http = RecordingHttp(
        status_code=403,
        payload={
            "status": "error",
            "errors": [{"errorCode": "UDAPI100060", "message": FAKE_TOKEN}],
        },
    )
    resolver = make_resolver(http)

    with pytest.raises(UpstoxInstrumentSearchError) as error:
        resolver.search("REL")

    assert str(error.value) == (
        "Upstox instrument search failed: HTTP 403; code=UDAPI100060"
    )
    assert FAKE_TOKEN not in str(error.value)
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_network_error_is_generic() -> None:
    http = RecordingHttp(error=OSError(f"failed {FAKE_TOKEN}"))
    resolver = make_resolver(http)

    with pytest.raises(
        UpstoxInstrumentSearchError, match="network error"
    ) as error:
        resolver.search("REL")
    assert FAKE_TOKEN not in str(error.value)


def test_missing_credential_is_generic() -> None:
    http = RecordingHttp()
    resolver = UpstoxInstrumentResolver(
        MemoryCredentialStore(), CREDENTIAL_ID, http_get=http
    )

    with pytest.raises(
        UpstoxInstrumentSearchError, match="credential error"
    ):
        resolver.search("REL")
    assert http.calls == []


def test_optional_filters_are_not_required_and_can_be_passed() -> None:
    http = RecordingHttp()
    resolver = make_resolver(http)

    resolver.search("NIFTY")
    resolver.search(
        "NIFTY",
        exchanges="NSE",
        segments="INDEX",
        instrument_types="INDEX",
    )

    first = parse_qs(urlparse(http.calls[0][0]).query)
    second = parse_qs(urlparse(http.calls[1][0]).query)
    assert "exchanges" not in first
    assert "segments" not in first
    assert second["exchanges"] == ["NSE"]
    assert second["segments"] == ["INDEX"]
    assert second["instrument_types"] == ["INDEX"]
