"""Offline tests for the Upstox V3 LTPC WebSocket adapter."""

import json
from datetime import datetime, timezone
from typing import Iterator

import pytest

import data.upstox_feed as upstox_module
from data.base_feed import PriceFeed, PriceUpdate
from data.upstox_feed import (
    UPSTOX_AUTHORIZE_URL,
    UPSTOX_USER_AGENT,
    UpstoxFeedError,
    UpstoxPriceFeed,
    _AUTHORIZATION_BODY_LIMIT,
    _FeedResponse,
    _close_winhttp_handles,
    _limit_authorization_body,
)

FAKE_TOKEN = "FAKE_UPSTOX_ACCESS_TOKEN_TEST_ONLY"
INSTRUMENT = "NSE_INDEX|Nifty 50"
TIMESTAMP_MS = 1_782_000_000_123


class FakeAuthorizationRequest:
    def __init__(
        self, url: str, credential: str, timeout: float, user_agent: str
    ) -> None:
        self.full_url = url
        self.timeout = timeout
        self._headers = {
            "Authorization": "Bearer " + credential,
            "Accept": "application/json",
            "User-agent": user_agent,
        }

    def get_header(self, name: str) -> str | None:
        return self._headers.get(name)


class FakeWebSocket:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = iter(responses or [])
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, message: bytes) -> None:
        self.sent.append(message)

    def recv(self, timeout: float) -> object:
        if self.closed:
            raise RuntimeError("WebSocket is closed")
        try:
            return next(self.responses)
        except StopIteration:
            raise TimeoutError

    def close(self) -> None:
        self.closed = True


def encode_ltpc(
    *,
    instrument: str = INSTRUMENT,
    price: float = 123.45,
    timestamp_ms: int = TIMESTAMP_MS,
) -> bytes:
    response = _FeedResponse()
    response.currentTs = timestamp_ms
    response.feeds[instrument].ltpc.ltp = price
    response.feeds[instrument].ltpc.ltt = timestamp_ms
    return response.SerializeToString()


def install_authorization(
    monkeypatch: pytest.MonkeyPatch,
    value: object,
    *,
    status_code: int = 200,
) -> list[FakeAuthorizationRequest]:
    requests: list[FakeAuthorizationRequest] = []

    def fake_authorize(
        url: str,
        credential: str,
        *,
        timeout: float,
        user_agent: str,
    ) -> tuple[int, bytes]:
        assert timeout > 0
        requests.append(
            FakeAuthorizationRequest(url, credential, timeout, user_agent)
        )
        if isinstance(value, bytes):
            return status_code, value
        return status_code, json.dumps(value).encode()

    monkeypatch.setattr(
        upstox_module, "_winhttp_get_authorization", fake_authorize
    )
    return requests


def install_authorization_failure(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> list[FakeAuthorizationRequest]:
    requests: list[FakeAuthorizationRequest] = []

    def fail_authorize(
        url: str,
        credential: str,
        *,
        timeout: float,
        user_agent: str,
    ) -> tuple[int, bytes]:
        requests.append(
            FakeAuthorizationRequest(url, credential, timeout, user_agent)
        )
        raise error

    monkeypatch.setattr(
        upstox_module, "_winhttp_get_authorization", fail_authorize
    )
    return requests


def test_upstox_feed_uses_v3_authorization_subscribes_and_normalizes_ltpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = install_authorization(
        monkeypatch,
        {
            "data": {
                "authorized_redirect_uri": "wss://feed.example.invalid/stream"
            }
        },
    )
    websocket = FakeWebSocket([encode_ltpc()])
    connected: list[str] = []

    def connect(uri: str, open_timeout: float) -> FakeWebSocket:
        assert open_timeout > 0
        connected.append(uri)
        return websocket

    feed = UpstoxPriceFeed(websocket_connect=connect)
    assert isinstance(feed, PriceFeed)
    feed.connect(INSTRUMENT, FAKE_TOKEN)
    updates = list(feed.stream(INSTRUMENT, count=1))

    assert connected == ["wss://feed.example.invalid/stream"]
    assert requests[0].full_url == (
        "https://api.upstox.com/v3/feed/market-data-feed/authorize"
    )
    assert requests[0].get_header("Authorization") == f"Bearer {FAKE_TOKEN}"
    assert requests[0].get_header("Accept") == "application/json"
    assert requests[0].get_header("User-agent") == UPSTOX_USER_AGENT
    assert isinstance(websocket.sent[0], bytes)
    subscription = json.loads(websocket.sent[0].decode("utf-8"))
    assert subscription["guid"]
    assert subscription["method"] == "sub"
    assert subscription["data"]["mode"] == "ltpc"
    assert subscription["data"]["instrumentKeys"] == [INSTRUMENT]
    assert updates == [
        PriceUpdate(
            INSTRUMENT,
            123.45,
            datetime.fromtimestamp(TIMESTAMP_MS / 1000, tz=timezone.utc),
        )
    ]
    feed.disconnect()
    assert websocket.closed


def test_upstox_feed_isolates_authorization_errors_from_fake_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_authorization_failure(
        monkeypatch,
        RuntimeError(f"failure: Bearer {FAKE_TOKEN}"),
    )
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert error.value.diagnostic_stage == "authorization request: network error"
    assert str(error.value) == (
        "Upstox authorization request failed: network error"
    )
    assert FAKE_TOKEN not in str(error.value)
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


@pytest.mark.parametrize("status_code", [401, 403])
def test_upstox_authorization_http_error_reports_only_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status_code: int,
) -> None:
    requests = install_authorization(
        monkeypatch,
        b"",
        status_code=status_code,
    )
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert str(error.value) == (
        f"Upstox authorization request failed: HTTP {status_code}"
    )
    assert error.value.diagnostic_stage == (
        f"authorization request: HTTP {status_code}"
    )
    assert requests[0].get_header("Authorization") == f"Bearer {FAKE_TOKEN}"
    assert requests[0].get_header("Accept") == "application/json"
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_upstox_authorization_network_failure_is_generic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_authorization_failure(
        monkeypatch, OSError(f"connection error includes {FAKE_TOKEN}")
    )
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert str(error.value) == (
        "Upstox authorization request failed: network error"
    )
    assert error.value.diagnostic_stage == "authorization request: network error"
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_upstox_authorization_endpoint_constant_is_v3_url() -> None:
    assert UPSTOX_AUTHORIZE_URL == (
        "https://api.upstox.com/v3/feed/market-data-feed/authorize"
    )


@pytest.mark.parametrize(
    "authorization",
    [
        {},
        {"data": {"authorized_redirect_uri": "http://not-secure.example/"}},
        {"data": {"authorized_redirect_uri": "wss://"}},
        {"data": {"authorizedRedirectUri": "wss://legacy.example/"}},
        b"not-json",
    ],
)
def test_upstox_feed_rejects_invalid_authorization_responses(
    monkeypatch: pytest.MonkeyPatch, authorization: object
) -> None:
    install_authorization(monkeypatch, authorization)
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError, match="invalid authorization") as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)
    assert error.value.diagnostic_stage == "invalid authorization response"


@pytest.mark.parametrize(
    ("status_code", "error_code", "error_message"),
    [
        (401, "UDAPI100050", "Invalid access token"),
        (403, "UDAPI100060", "Insufficient authorization"),
    ],
)
def test_upstox_authorization_http_error_reports_safe_json_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status_code: int,
    error_code: str,
    error_message: str,
) -> None:
    body = json.dumps(
        {
            "status": "error",
            "errors": [
                {"errorCode": error_code, "message": error_message}
            ],
        }
    ).encode()
    requests = install_authorization(
        monkeypatch, body, status_code=status_code
    )
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert str(error.value) == (
        f"Upstox authorization request failed: HTTP {status_code}; "
        f"code={error_code}; message={error_message}"
    )
    assert error.value.diagnostic_stage == (
        f"authorization request: HTTP {status_code}"
    )
    assert error.value.diagnostic_code == error_code
    assert error.value.diagnostic_message == error_message
    assert requests[0].get_header("Authorization") == (
        "Bearer " + FAKE_TOKEN
    )
    assert requests[0].get_header("Accept") == "application/json"
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "error",
            "errorCode": "UDAPI100060",
            "message": "Insufficient authorization",
        },
        {
            "status": "error",
            "errors": {
                "errorCode": "UDAPI100060",
                "message": "Insufficient authorization",
            },
        },
        {
            "status": "error",
            "data": {
                "errors": [
                    {
                        "error_code": "UDAPI100060",
                        "error_message": "Insufficient authorization",
                    }
                ]
            },
        },
    ],
)
def test_upstox_authorization_http_layer_error_envelopes(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    install_authorization(monkeypatch, payload, status_code=403)
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert error.value.diagnostic_stage == "authorization request: HTTP 403"
    assert error.value.diagnostic_code == "UDAPI100060"
    assert error.value.diagnostic_message == "Insufficient authorization"
    assert str(error.value) == (
        "Upstox authorization request failed: HTTP 403; "
        "code=UDAPI100060; message=Insufficient authorization"
    )


def test_upstox_authorization_extracts_actual_top_level_error_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = json.dumps(
        {
            "type": "about:blank",
            "title": "Forbidden",
            "status": 403,
            "detail": "Authorization is not available for this request.",
            "instance": "/request/sensitive-instance",
            "error_code": "UDAPI100060",
            "error_name": "AccessDenied",
            "error_category": "AUTHORIZATION",
            "ray_id": "sensitive-ray-id",
            "timestamp": "2026-10-10T00:00:00Z",
            "zone": "example-zone",
            "cloudflare_error": {"internal": "sensitive response content"},
            "retryable": False,
            "owner_action_required": True,
            "what_you_should_do": "Review the application API permissions.",
        }
    ).encode()

    install_authorization(monkeypatch, response_body, status_code=403)
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert error.value.diagnostic_code == "UDAPI100060"
    assert error.value.diagnostic_error_name == "AccessDenied"
    assert error.value.diagnostic_detail == (
        "Authorization is not available for this request."
    )
    assert error.value.diagnostic_error_category == "AUTHORIZATION"
    assert error.value.diagnostic_retryable is False
    assert error.value.diagnostic_owner_action_required is True
    assert error.value.diagnostic_what_you_should_do == (
        "Review the application API permissions."
    )
    assert str(error.value) == (
        "Upstox authorization request failed: HTTP 403; "
        "code=UDAPI100060; name=AccessDenied; "
        "detail=Authorization is not available for this request.; "
        "category=AUTHORIZATION; retryable=false; "
        "owner_action_required=true; "
        "what_you_should_do=Review the application API permissions."
    )
    for sensitive_value in (
        FAKE_TOKEN,
        "/request/sensitive-instance",
        "sensitive-ray-id",
        "sensitive response content",
        "about:blank",
        "example-zone",
    ):
        assert sensitive_value not in str(error.value)


def test_upstox_authorization_missing_top_level_optional_fields_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = json.dumps(
        {"error_code": "UDAPI100060"}
    ).encode()

    install_authorization(monkeypatch, response_body, status_code=403)
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert error.value.diagnostic_code == "UDAPI100060"
    assert error.value.diagnostic_error_name is None
    assert error.value.diagnostic_detail is None
    assert error.value.diagnostic_error_category is None
    assert error.value.diagnostic_retryable is None
    assert error.value.diagnostic_owner_action_required is None
    assert error.value.diagnostic_what_you_should_do is None
    assert str(error.value) == (
        "Upstox authorization request failed: HTTP 403; code=UDAPI100060"
    )


def test_upstox_authorization_drops_sensitive_top_level_error_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    response_body = json.dumps(
        {
            "error_code": FAKE_TOKEN,
            "error_name": FAKE_TOKEN,
            "detail": "Credential: " + FAKE_TOKEN,
            "error_category": FAKE_TOKEN,
            "retryable": False,
            "owner_action_required": True,
            "what_you_should_do": "Visit https://example.invalid/" + FAKE_TOKEN,
            "instance": "/request/private",
            "ray_id": "private-ray",
        }
    ).encode()

    install_authorization(monkeypatch, response_body, status_code=403)
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    assert error.value.diagnostic_code is None
    assert error.value.diagnostic_error_name is None
    assert error.value.diagnostic_detail is None
    assert error.value.diagnostic_error_category is None
    assert error.value.diagnostic_retryable is False
    assert error.value.diagnostic_owner_action_required is True
    assert error.value.diagnostic_what_you_should_do is None
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err
    assert "/request/private" not in str(error.value)
    assert "private-ray" not in str(error.value)


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        (b"not JSON", None),
        (
            json.dumps(
                {
                    "errors": [
                        {
                            "errorCode": "UDAPI100050",
                            "message": "Request echoed " + FAKE_TOKEN,
                        }
                    ]
                }
            ).encode(),
            "UDAPI100050",
        ),
    ],
)
def test_upstox_authorization_http_error_falls_back_without_unsafe_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    body: bytes,
    expected_code: str | None,
) -> None:
    install_authorization(monkeypatch, body, status_code=403)
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)

    with pytest.raises(UpstoxFeedError) as error:
        feed.connect(INSTRUMENT, FAKE_TOKEN)

    expected = "Upstox authorization request failed: HTTP 403"
    if expected_code is not None:
        expected += f"; code={expected_code}"
    assert str(error.value) == expected
    assert error.value.diagnostic_code == expected_code
    assert error.value.diagnostic_message is None
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_upstox_feed_reports_websocket_and_subscription_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_authorization(
        monkeypatch,
        {"data": {"authorized_redirect_uri": "wss://feed.example.invalid/"}},
    )
    with pytest.raises(
        UpstoxFeedError, match="WebSocket connection"
    ) as connection_error:
        UpstoxPriceFeed(
            websocket_connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError(FAKE_TOKEN)
            )
        ).connect(INSTRUMENT, FAKE_TOKEN)
    assert connection_error.value.diagnostic_stage == "WebSocket connection"

    websocket = FakeWebSocket()

    def fail_send(_message: bytes) -> None:
        raise RuntimeError(FAKE_TOKEN)

    websocket.send = fail_send
    with pytest.raises(
        UpstoxFeedError, match="subscription"
    ) as subscription_error:
        UpstoxPriceFeed(
            websocket_connect=lambda *_args, **_kwargs: websocket
        ).connect(INSTRUMENT, FAKE_TOKEN)
    assert subscription_error.value.diagnostic_stage == "subscription"
    assert websocket.closed


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\x12\x05x", "malformed protobuf"),
        (encode_ltpc(price=0, timestamp_ms=0), "invalid LTPC"),
    ],
)
def test_upstox_feed_rejects_malformed_and_invalid_feed_messages(
    payload: bytes, message: str
) -> None:
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)
    websocket = FakeWebSocket([payload])
    feed._websocket = websocket
    feed._instruments = (INSTRUMENT,)

    with pytest.raises(UpstoxFeedError, match=message):
        list(feed.stream(INSTRUMENT, count=1))


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_upstox_feed_rejects_nonpositive_or_nonfinite_prices(price: float) -> None:
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)
    websocket = FakeWebSocket([encode_ltpc(price=price)])
    feed._websocket = websocket
    feed._instruments = (INSTRUMENT,)

    with pytest.raises(UpstoxFeedError, match="invalid LTPC"):
        list(feed.stream(INSTRUMENT, count=1))


def test_upstox_feed_stops_cleanly_on_disconnect() -> None:
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)
    websocket = FakeWebSocket([encode_ltpc()])
    feed._websocket = websocket
    feed._instruments = (INSTRUMENT,)
    updates: Iterator[PriceUpdate] = feed.stream(INSTRUMENT)

    assert next(updates).price == 123.45
    feed.disconnect()

    assert list(updates) == []
    assert websocket.closed
    assert feed._instruments == ()


def test_upstox_feed_rejects_invalid_arguments() -> None:
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="credential"):
        feed.connect(INSTRUMENT, "")
    with pytest.raises(ValueError, match="instrument"):
        feed.connect((), FAKE_TOKEN)
    with pytest.raises(ValueError, match="instrument"):
        feed.connect(["  ", ""], FAKE_TOKEN)
    with pytest.raises(ValueError, match="count"):
        list(feed.stream(INSTRUMENT, count=-1))


def test_authorization_body_is_bounded_to_16_kib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {
            "data": {
                "authorized_redirect_uri": "wss://feed.example.invalid/stream"
            }
        }
    ).encode()
    oversized = payload + (b" " * (_AUTHORIZATION_BODY_LIMIT + 8))
    assert len(_limit_authorization_body(oversized)) == _AUTHORIZATION_BODY_LIMIT
    websocket = FakeWebSocket([encode_ltpc()])
    install_authorization(monkeypatch, oversized)
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: websocket)
    feed.connect(INSTRUMENT, FAKE_TOKEN)
    assert list(feed.stream(INSTRUMENT, count=1))[0].instrument == INSTRUMENT
    feed.disconnect()


def test_winhttp_handles_are_closed_after_a_close_failure() -> None:
    closed: list[object] = []

    def close(handle: object) -> None:
        closed.append(handle)
        if handle == "connect":
            raise RuntimeError("close failed")

    with pytest.raises(RuntimeError, match="close failed"):
        _close_winhttp_handles(close, "request", "connect", "session")
    assert closed == ["request", "connect", "session"]


def test_winhttp_handle_cleanup_skips_missing_handles() -> None:
    closed: list[object] = []
    _close_winhttp_handles(closed.append, None, "session")
    assert closed == ["session"]


def test_winhttp_authorization_uses_documented_secure_no_proxy_constants() -> None:
    assert upstox_module._WINHTTP_ACCESS_TYPE_NO_PROXY == 1
    assert upstox_module._WINHTTP_FLAG_SECURE == 0x00800000
    assert upstox_module._AUTHORIZATION_BODY_LIMIT == 16_384


def test_upstox_feed_subscribes_unique_keys_and_yields_each_ltpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reliance = "NSE_EQ|INE002A01018"
    unknown = "NSE_EQ|UNKNOWN"
    install_authorization(
        monkeypatch,
        {"data": {"authorized_redirect_uri": "wss://feed.example.invalid/"}},
    )
    payload = _FeedResponse()
    payload.currentTs = TIMESTAMP_MS
    payload.feeds[INSTRUMENT].ltpc.ltp = 22000.5
    payload.feeds[INSTRUMENT].ltpc.ltt = TIMESTAMP_MS
    payload.feeds[reliance].ltpc.ltp = 1400.25
    payload.feeds[reliance].ltpc.ltt = TIMESTAMP_MS
    payload.feeds[unknown].ltpc.ltp = 1.0
    payload.feeds[unknown].ltpc.ltt = TIMESTAMP_MS
    websocket = FakeWebSocket([payload.SerializeToString()])
    feed = UpstoxPriceFeed(websocket_connect=lambda *_args, **_kwargs: websocket)

    feed.connect((INSTRUMENT, reliance, INSTRUMENT, "  "), FAKE_TOKEN)
    updates = list(feed.stream(INSTRUMENT, count=2))

    subscription = json.loads(websocket.sent[0].decode("utf-8"))
    assert subscription["data"]["instrumentKeys"] == [INSTRUMENT, reliance]
    assert {(update.instrument, update.price) for update in updates} == {
        (INSTRUMENT, 22000.5),
        (reliance, 1400.25),
    }
    feed.disconnect()
    assert websocket.closed
    assert feed._instruments == ()
