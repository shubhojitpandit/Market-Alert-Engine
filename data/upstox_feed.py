"""Direct Upstox Market Data Feed V3 WebSocket adapter."""

import json
import math
import re
import threading
import uuid
from ctypes import (
    POINTER,
    WinDLL,
    byref,
    c_size_t,
    c_ushort,
    c_wchar_p,
    create_string_buffer,
    get_last_error,
    sizeof,
)
from ctypes import wintypes
from datetime import datetime, timezone
from importlib import import_module
from collections.abc import Sequence
from typing import Callable, Iterator
from urllib.parse import urlparse

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError

from data.base_feed import PriceFeed, PriceUpdate

UPSTOX_AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
UPSTOX_USER_AGENT = "MarketAlertEngine/1.0"
_AUTHORIZATION_BODY_LIMIT = 16_384

# Documented WinHTTP constants from winhttp.h.
_WINHTTP_ACCESS_TYPE_NO_PROXY = 1
_WINHTTP_FLAG_SECURE = 0x00800000
_INTERNET_DEFAULT_HTTPS_PORT = 443
_WINHTTP_ADDREQ_FLAG_ADD = 0x20000000
_WINHTTP_QUERY_STATUS_CODE = 19
_WINHTTP_QUERY_FLAG_NUMBER = 0x20000000
_WINHTTP_HEADER_LENGTH_NULL_TERMINATED = 0xFFFFFFFF

_HINTERNET = wintypes.LPVOID
_INTERNET_PORT = c_ushort


class UpstoxFeedError(RuntimeError):
    """Raised when Upstox cannot provide valid live market data."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic_stage: str | None = None,
        diagnostic_code: str | None = None,
        diagnostic_message: str | None = None,
        diagnostic_detail: str | None = None,
        diagnostic_error_name: str | None = None,
        diagnostic_error_category: str | None = None,
        diagnostic_retryable: bool | None = None,
        diagnostic_owner_action_required: bool | None = None,
        diagnostic_what_you_should_do: str | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic_stage = diagnostic_stage
        self.diagnostic_code = diagnostic_code
        self.diagnostic_message = diagnostic_message
        self.diagnostic_detail = diagnostic_detail
        self.diagnostic_error_name = diagnostic_error_name
        self.diagnostic_error_category = diagnostic_error_category
        self.diagnostic_retryable = diagnostic_retryable
        self.diagnostic_owner_action_required = (
            diagnostic_owner_action_required
        )
        self.diagnostic_what_you_should_do = diagnostic_what_you_should_do

def _build_feed_response_class() -> type:
    """Build the minimal V3 protobuf schema needed for LTPC updates."""
    file_descriptor = descriptor_pb2.FileDescriptorProto()
    file_descriptor.name = "upstox_feed_response.proto"
    file_descriptor.package = "upstox"
    file_descriptor.syntax = "proto3"

    ltpc = file_descriptor.message_type.add()
    ltpc.name = "LTPC"
    for name, number, field_type in (
        ("ltp", 1, descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE),
        ("ltt", 2, descriptor_pb2.FieldDescriptorProto.TYPE_INT64),
        ("ltq", 3, descriptor_pb2.FieldDescriptorProto.TYPE_INT64),
        ("cp", 4, descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE),
    ):
        field = ltpc.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = field_type

    feed = file_descriptor.message_type.add()
    feed.name = "Feed"
    field = feed.field.add()
    field.name = "ltpc"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    field.type_name = ".upstox.LTPC"

    response = file_descriptor.message_type.add()
    response.name = "FeedResponse"
    response_type = response.field.add()
    response_type.name = "type"
    response_type.number = 1
    response_type.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    response_type.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    entry = response.nested_type.add()
    entry.name = "FeedsEntry"
    entry.options.map_entry = True
    key = entry.field.add()
    key.name = "key"
    key.number = 1
    key.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    key.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    value = entry.field.add()
    value.name = "value"
    value.number = 2
    value.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    value.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    value.type_name = ".upstox.Feed"

    feeds = response.field.add()
    feeds.name = "feeds"
    feeds.number = 2
    feeds.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    feeds.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    feeds.type_name = ".upstox.FeedResponse.FeedsEntry"
    current_ts = response.field.add()
    current_ts.name = "currentTs"
    current_ts.number = 3
    current_ts.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    current_ts.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64

    descriptor = descriptor_pool.Default().Add(file_descriptor)
    return message_factory.GetMessageClass(
        descriptor.message_types_by_name["FeedResponse"]
    )


_FeedResponse = _build_feed_response_class()


def _limit_authorization_body(body: bytes) -> bytes:
    """Keep authorization payloads within the existing 16 KiB bound."""
    return body[:_AUTHORIZATION_BODY_LIMIT]


def _close_winhttp_handles(close: Callable[[object], object], *handles: object) -> None:
    """Close WinHTTP handles; later handles are still closed if one close fails."""
    last_error: Exception | None = None
    for handle in handles:
        if not handle:
            continue
        try:
            close(handle)
        except Exception as error:
            last_error = error
    if last_error is not None:
        raise last_error


def _load_winhttp() -> WinDLL:
    dll = WinDLL("winhttp", use_last_error=True)
    dll.WinHttpOpen.restype = _HINTERNET
    dll.WinHttpOpen.argtypes = [
        c_wchar_p,
        wintypes.DWORD,
        c_wchar_p,
        c_wchar_p,
        wintypes.DWORD,
    ]
    dll.WinHttpConnect.restype = _HINTERNET
    dll.WinHttpConnect.argtypes = [
        _HINTERNET,
        c_wchar_p,
        _INTERNET_PORT,
        wintypes.DWORD,
    ]
    dll.WinHttpOpenRequest.restype = _HINTERNET
    dll.WinHttpOpenRequest.argtypes = [
        _HINTERNET,
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        c_wchar_p,
        POINTER(c_wchar_p),
        wintypes.DWORD,
    ]
    dll.WinHttpAddRequestHeaders.restype = wintypes.BOOL
    dll.WinHttpAddRequestHeaders.argtypes = [
        _HINTERNET,
        c_wchar_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    dll.WinHttpSetTimeouts.restype = wintypes.BOOL
    dll.WinHttpSetTimeouts.argtypes = [
        _HINTERNET,
        wintypes.INT,
        wintypes.INT,
        wintypes.INT,
        wintypes.INT,
    ]
    dll.WinHttpSendRequest.restype = wintypes.BOOL
    dll.WinHttpSendRequest.argtypes = [
        _HINTERNET,
        c_wchar_p,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        c_size_t,
    ]
    dll.WinHttpReceiveResponse.restype = wintypes.BOOL
    dll.WinHttpReceiveResponse.argtypes = [_HINTERNET, wintypes.LPVOID]
    dll.WinHttpQueryHeaders.restype = wintypes.BOOL
    dll.WinHttpQueryHeaders.argtypes = [
        _HINTERNET,
        wintypes.DWORD,
        c_wchar_p,
        wintypes.LPVOID,
        POINTER(wintypes.DWORD),
        POINTER(wintypes.DWORD),
    ]
    dll.WinHttpReadData.restype = wintypes.BOOL
    dll.WinHttpReadData.argtypes = [
        _HINTERNET,
        wintypes.LPVOID,
        wintypes.DWORD,
        POINTER(wintypes.DWORD),
    ]
    dll.WinHttpCloseHandle.restype = wintypes.BOOL
    dll.WinHttpCloseHandle.argtypes = [_HINTERNET]
    return dll


def _winhttp_get_authorization(
    url: str,
    credential: str,
    *,
    timeout: float,
    user_agent: str,
) -> tuple[int, bytes]:
    """GET the Upstox V3 authorize URL through WinHTTP/Schannel.

    This helper is provider-specific and is not a general HTTP client.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise OSError("authorization request is not HTTPS")

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
        remaining = _AUTHORIZATION_BODY_LIMIT
        while remaining > 0:
            buf = create_string_buffer(min(4096, remaining))
            got = wintypes.DWORD(0)
            if not dll.WinHttpReadData(request, buf, len(buf), byref(got)):
                raise OSError(get_last_error())
            if got.value == 0:
                break
            chunks.append(buf.raw[: got.value])
            remaining -= got.value
        return int(status.value), _limit_authorization_body(b"".join(chunks))
    finally:
        headers = None
        try:
            _close_winhttp_handles(
                dll.WinHttpCloseHandle, request, connect, session
            )
        except Exception:
            pass


def _normalize_subscription_keys(
    instrument: str | Sequence[str],
) -> tuple[str, ...]:
    """Return unique subscribed instrument keys, preserving order."""
    values: Sequence[str]
    if isinstance(instrument, str):
        values = (instrument,)
    else:
        values = instrument
    keys: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("instrument must not be empty")
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        raise ValueError("instrument must not be empty")
    return tuple(keys)


class UpstoxPriceFeed(PriceFeed):
    """Subscribe to one or more Upstox V3 instruments in LTPC mode over WebSocket."""

    def __init__(
        self,
        *,
        websocket_connect: Callable[..., object] | None = None,
        authorize_url: str = UPSTOX_AUTHORIZE_URL,
        timeout: float = 10.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._websocket_connect = websocket_connect
        self._authorize_url = authorize_url
        self._timeout = timeout
        self._websocket: object | None = None
        self._instruments: tuple[str, ...] = ()
        self._lock = threading.Lock()

    def connect(
        self, instrument: str | Sequence[str], credential: str
    ) -> None:
        """Authorize and subscribe, without retaining or exposing the token."""
        keys = _normalize_subscription_keys(instrument)
        if not credential:
            raise ValueError("credential must not be empty")
        with self._lock:
            if self._websocket is not None:
                raise UpstoxFeedError("Upstox feed is already connected")

        redirect_uri = self._get_authorized_uri(credential)
        try:
            websocket = self._get_websocket_connect()(
                redirect_uri, open_timeout=self._timeout
            )
        except Exception:
            raise UpstoxFeedError(
                "Upstox WebSocket connection failed",
                diagnostic_stage="WebSocket connection",
            ) from None

        try:
            request = {
                "guid": uuid.uuid4().hex,
                "method": "sub",
                "data": {"mode": "ltpc", "instrumentKeys": list(keys)},
            }
            websocket.send(json.dumps(request).encode("utf-8"))
        except Exception:
            try:
                websocket.close()
            except Exception:
                pass
            raise UpstoxFeedError(
                "Upstox WebSocket subscription failed",
                diagnostic_stage="subscription",
            ) from None

        with self._lock:
            self._websocket = websocket
            self._instruments = keys

    def disconnect(self) -> None:
        """Close the active WebSocket without affecting saved credentials."""
        with self._lock:
            websocket = self._websocket
            self._websocket = None
            self._instruments = ()
        if websocket is None:
            return
        try:
            websocket.close()
        except Exception:
            raise UpstoxFeedError("Could not close the Upstox WebSocket") from None

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        """Yield protobuf LTPC updates until closed or count is reached."""
        if not instrument:
            raise ValueError("instrument must not be empty")
        if count is not None and count < 0:
            raise ValueError("count must not be negative")
        if count == 0:
            return
        with self._lock:
            websocket = self._websocket
            subscribed = self._instruments
        if websocket is None or instrument not in subscribed:
            raise UpstoxFeedError("Upstox feed is not connected to this instrument")

        emitted = 0
        while count is None or emitted < count:
            try:
                payload = websocket.recv(timeout=1)
            except TimeoutError:
                with self._lock:
                    if self._websocket is not websocket:
                        return
                continue
            except Exception:
                with self._lock:
                    if self._websocket is not websocket:
                        return
                raise UpstoxFeedError("Upstox WebSocket receive failed") from None

            if not isinstance(payload, bytes):
                raise UpstoxFeedError("Upstox returned a non-binary feed message")
            updates = self._decode(payload)
            for update in updates:
                if update.instrument not in subscribed:
                    continue
                yield update
                emitted += 1
                if count is not None and emitted >= count:
                    return

    def _get_authorized_uri(self, credential: str) -> str:
        try:
            status_code, authorization_payload = _winhttp_get_authorization(
                self._authorize_url,
                credential,
                timeout=self._timeout,
                user_agent=UPSTOX_USER_AGENT,
            )
        except Exception:
            raise UpstoxFeedError(
                "Upstox authorization request failed: network error",
                diagnostic_stage="authorization request: network error",
            ) from None

        authorization_payload = _limit_authorization_body(authorization_payload)
        if not isinstance(status_code, int) or not (100 <= status_code <= 599):
            raise UpstoxFeedError(
                "Upstox authorization request failed: network error",
                diagnostic_stage="authorization request: network error",
            )
        if status_code != 200:
            diagnostic_code = None
            diagnostic_message = None
            diagnostic_detail = None
            diagnostic_error_name = None
            diagnostic_error_category = None
            diagnostic_retryable = None
            diagnostic_owner_action_required = None
            diagnostic_what_you_should_do = None
            if status_code in (401, 403):
                details = _extract_authorization_error_details(
                    authorization_payload, credential
                )
                diagnostic_code = details["code"]
                diagnostic_error_name = details["error_name"]
                diagnostic_message = details["message"]
                diagnostic_detail = details["detail"]
                diagnostic_error_category = details["error_category"]
                diagnostic_retryable = details["retryable"]
                diagnostic_owner_action_required = details[
                    "owner_action_required"
                ]
                diagnostic_what_you_should_do = details[
                    "what_you_should_do"
                ]
            diagnostic_stage = f"authorization request: HTTP {status_code}"
            message = (
                f"Upstox authorization request failed: HTTP {status_code}"
            )
            if diagnostic_code is not None:
                message += f"; code={diagnostic_code}"
            if diagnostic_error_name is not None:
                message += f"; name={diagnostic_error_name}"
            if diagnostic_message is not None:
                message += f"; message={diagnostic_message}"
            if diagnostic_detail is not None:
                message += f"; detail={diagnostic_detail}"
            if diagnostic_error_category is not None:
                message += f"; category={diagnostic_error_category}"
            if diagnostic_retryable is not None:
                message += f"; retryable={str(diagnostic_retryable).lower()}"
            if diagnostic_owner_action_required is not None:
                message += (
                    "; owner_action_required="
                    f"{str(diagnostic_owner_action_required).lower()}"
                )
            if diagnostic_what_you_should_do is not None:
                message += (
                    "; what_you_should_do="
                    f"{diagnostic_what_you_should_do}"
                )
            raise UpstoxFeedError(
                message,
                diagnostic_stage=diagnostic_stage,
                diagnostic_code=diagnostic_code,
                diagnostic_message=diagnostic_message,
                diagnostic_detail=diagnostic_detail,
                diagnostic_error_name=diagnostic_error_name,
                diagnostic_error_category=diagnostic_error_category,
                diagnostic_retryable=diagnostic_retryable,
                diagnostic_owner_action_required=(
                    diagnostic_owner_action_required
                ),
                diagnostic_what_you_should_do=(
                    diagnostic_what_you_should_do
                ),
            ) from None

        try:
            authorization = json.loads(authorization_payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise UpstoxFeedError(
                "Upstox returned an invalid authorization response",
                diagnostic_stage="invalid authorization response",
            ) from None

        try:
            redirect_uri = authorization["data"]["authorized_redirect_uri"]
            parsed_uri = urlparse(redirect_uri)
        except (KeyError, TypeError, ValueError):
            raise UpstoxFeedError(
                "Upstox returned an invalid authorization response",
                diagnostic_stage="invalid authorization response",
            ) from None
        if (
            not isinstance(redirect_uri, str)
            or parsed_uri.scheme != "wss"
            or not parsed_uri.netloc
        ):
            raise UpstoxFeedError(
                "Upstox returned an invalid authorization response",
                diagnostic_stage="invalid authorization response",
            )
        return redirect_uri


    def _get_websocket_connect(self) -> Callable[..., object]:
        if self._websocket_connect is not None:
            return self._websocket_connect
        try:
            module = import_module("websockets.sync.client")
        except ModuleNotFoundError:
            raise UpstoxFeedError(
                "The websockets package is required for Upstox feeds"
            ) from None
        return module.connect

    @staticmethod
    def _decode(payload: bytes) -> list[PriceUpdate]:
        response = _FeedResponse()
        try:
            response.ParseFromString(payload)
        except DecodeError:
            raise UpstoxFeedError(
                "Upstox returned a malformed protobuf feed message"
            ) from None

        updates: list[PriceUpdate] = []
        for instrument, feed in response.feeds.items():
            if not feed.HasField("ltpc"):
                continue
            price = feed.ltpc.ltp
            timestamp_ms = feed.ltpc.ltt or response.currentTs
            if not math.isfinite(price) or price <= 0 or timestamp_ms <= 0:
                raise UpstoxFeedError(
                    "Upstox returned an invalid LTPC market-data update"
                )
            try:
                timestamp = datetime.fromtimestamp(
                    timestamp_ms / 1000, tz=timezone.utc
                )
            except (OSError, OverflowError, ValueError):
                raise UpstoxFeedError(
                    "Upstox returned an invalid LTPC timestamp"
                ) from None
            updates.append(
                PriceUpdate(
                    instrument=instrument,
                    price=price,
                    timestamp=timestamp,
                )
            )
        return updates


_SAFE_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_SAFE_ERROR_NAME = re.compile(r"[A-Za-z0-9_. -]{1,64}\Z")
_SAFE_ERROR_CATEGORY = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_SAFE_ERROR_MESSAGE = re.compile(r"[A-Za-z0-9 .,;:!?()'/-]{1,300}\Z")
_SENSITIVE_ERROR_CONTENT = re.compile(
    r"(?:https?://|wss?://|www\.|bearer\s|authorization\s*:|"
    r"\b(?:token|secret|credential)\s*[:=])",
    re.IGNORECASE,
)


def _extract_authorization_error_details(
    response_body: bytes, credential: str
) -> dict[str, str | bool | None]:
    details: dict[str, str | bool | None] = {
        "code": None,
        "error_name": None,
        "message": None,
        "detail": None,
        "error_category": None,
        "retryable": None,
        "owner_action_required": None,
        "what_you_should_do": None,
    }
    try:
        payload = json.loads(response_body)
        error_detail = _authorization_error_object(payload)
        if error_detail is None:
            return details
    except Exception:
        return details

    code = (
        error_detail.get("error_code")
        or error_detail.get("errorCode")
        or error_detail.get("error_code")
        or error_detail.get("code")
    )
    error_name = (
        error_detail.get("error_name")
        or error_detail.get("errorName")
    )
    message = (
        error_detail.get("message")
        or error_detail.get("errorMessage")
        or error_detail.get("error_message")
        or error_detail.get("error_description")
    )
    detail = error_detail.get("detail")
    error_category = error_detail.get("error_category")
    what_you_should_do = error_detail.get("what_you_should_do")

    if (
        isinstance(code, str)
        and _SAFE_ERROR_CODE.fullmatch(code)
        and not (credential and credential in code)
    ):
        details["code"] = code
    if (
        isinstance(error_name, str)
        and _SAFE_ERROR_NAME.fullmatch(error_name)
        and not (credential and credential in error_name)
    ):
        details["error_name"] = error_name
    if _is_safe_error_text(message, credential):
        details["message"] = message
    if _is_safe_error_text(detail, credential):
        details["detail"] = detail
    if (
        isinstance(error_category, str)
        and _SAFE_ERROR_CATEGORY.fullmatch(error_category)
        and not (credential and credential in error_category)
    ):
        details["error_category"] = error_category
    if type(error_detail.get("retryable")) is bool:
        details["retryable"] = error_detail["retryable"]
    if type(error_detail.get("owner_action_required")) is bool:
        details["owner_action_required"] = error_detail[
            "owner_action_required"
        ]
    if _is_safe_error_text(what_you_should_do, credential):
        details["what_you_should_do"] = what_you_should_do
    return details


def _is_safe_error_text(value: object, credential: str) -> bool:
    return (
        isinstance(value, str)
        and bool(_SAFE_ERROR_MESSAGE.fullmatch(value))
        and not (credential and credential in value)
        and not _SENSITIVE_ERROR_CONTENT.search(value)
    )


def _authorization_error_object(payload: object) -> dict[str, object] | None:
    if isinstance(payload, list):
        return next(
            (item for item in payload if isinstance(item, dict)), None
        )
    if not isinstance(payload, dict):
        return None

    errors = payload.get("errors")
    if isinstance(errors, list):
        return next(
            (item for item in errors if isinstance(item, dict)), None
        )
    if isinstance(errors, dict):
        return errors

    error = payload.get("error")
    if isinstance(error, dict):
        return error

    data = payload.get("data")
    if isinstance(data, dict):
        nested_error = _authorization_error_object(data)
        if nested_error is not None:
            return nested_error

    if any(
        key in payload
        for key in (

            "error_name",
            "detail",
            "error_category",
            "what_you_should_do",
            "errorCode",
            "error_code",
            "code",
            "message",
            "errorMessage",
            "error_message",
            "error_description",
        )
    ):
        return payload
    return None
