"""Diagnostic-only runtime checks and WebSocket connection logging."""

from __future__ import annotations

import asyncio
import re
import ssl
import sys
import threading
import traceback
from urllib.parse import urlsplit


def _redact(text: str) -> str:
    text = re.sub(
        r"\b(?:https?|wss?)://[^\s\"'<>]+",
        "[URL REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+",
        r"\1 [REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"token|secret|password|authorization)\b(\s*[:=]\s*)"
        r"([^\s,;]+|\"[^\"]*\"|'[^']*')",
        r"\1\2[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(payload|market[_ -]?data|body)\b(\s*[:=]\s*)"
        r"([^\r\n]+)",
        r"\1\2[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _report_failure(
    provider: str, stage: str, error: BaseException
) -> None:
    error_type = type(error)
    formatted = traceback.TracebackException.from_exception(
        error,
        limit=None,
        lookup_lines=False,
        capture_locals=False,
        compact=True,
    )
    print(
        f"[DIAGNOSTIC] {provider} {stage} FAILED",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"Exception type: {error_type.__module__}.{error_type.__qualname__}",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"Exception message: {_redact(str(error))}",
        file=sys.stderr,
        flush=True,
    )
    print("Full traceback:", file=sys.stderr, flush=True)
    print(
        _redact("".join(formatted.format(chain=True))),
        file=sys.stderr,
        flush=True,
    )


print("[DIAGNOSTIC] runtime hook loaded", file=sys.stderr, flush=True)
print(f"[DIAGNOSTIC] Python version: {sys.version}", file=sys.stderr, flush=True)

websockets = None
websocket_client = None
try:
    import websockets as imported_websockets

    websockets = imported_websockets
except Exception as error:
    print(
        f"[DIAGNOSTIC] websockets import failed: "
        f"{type(error).__module__}.{type(error).__qualname__}: "
        f"{_redact(str(error))}",
        file=sys.stderr,
        flush=True,
    )

print(
    f"[DIAGNOSTIC] websockets version: "
    f"{getattr(websockets, '__version__', 'unavailable')}",
    file=sys.stderr,
    flush=True,
)
print(
    f"[DIAGNOSTIC] websockets module path: "
    f"{getattr(websockets, '__file__', 'unavailable')}",
    file=sys.stderr,
    flush=True,
)

try:
    if websockets is None:
        raise RuntimeError("websockets package import failed")
    from websockets.sync import client as websocket_client

    if not callable(websocket_client.connect):
        raise RuntimeError("websockets.sync.client.connect is unavailable")
    event_loop = asyncio.new_event_loop()
    event_loop.close()
    ssl.create_default_context()
    print(
        "[DIAGNOSTIC] WebSocket runtime initialization: OK",
        file=sys.stderr,
        flush=True,
    )
except Exception as error:
    print(
        "[DIAGNOSTIC] WebSocket runtime initialization: FAILED",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"Exception type: {type(error).__module__}.{type(error).__qualname__}",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"Exception message: {_redact(str(error))}",
        file=sys.stderr,
        flush=True,
    )

print("[DIAGNOSTIC] asyncio import: OK", file=sys.stderr, flush=True)
print("[DIAGNOSTIC] ssl import: OK", file=sys.stderr, flush=True)

from data.delta_feed import DeltaPriceFeed
from data.upstox_feed import UpstoxPriceFeed
from data.connection_manager import ConnectionManager


class _DiagnosticWebSocket:
    def __init__(self, websocket: object, provider: str) -> None:
        self._websocket = websocket
        self._provider = provider
        self._first_receive = True

    def __getattr__(self, name: str) -> object:
        return getattr(self._websocket, name)

    def send(self, *args: object, **kwargs: object) -> object:
        print(
            f"[DIAGNOSTIC] {self._provider} subscription SEND starting",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = self._websocket.send(*args, **kwargs)
        except Exception as error:
            _report_failure(self._provider, "subscription SEND", error)
            raise
        print(
            f"[DIAGNOSTIC] {self._provider} subscription SEND complete",
            file=sys.stderr,
            flush=True,
        )
        return result

    def recv(self, *args: object, **kwargs: object) -> object:
        first_receive = self._first_receive
        if first_receive:
            self._first_receive = False
            kwargs["timeout"] = 10
            print(
                f"[DIAGNOSTIC] {self._provider} waiting for first message",
                file=sys.stderr,
                flush=True,
            )
        try:
            result = self._websocket.recv(*args, **kwargs)
        except TimeoutError:
            if first_receive:
                print(
                    f"[DIAGNOSTIC] {self._provider} FIRST MESSAGE TIMEOUT "
                    "after 10 seconds",
                    file=sys.stderr,
                    flush=True,
                )
            raise
        except Exception as error:
            _report_failure(self._provider, "receive", error)
            raise
        if first_receive:
            print(
                f"[DIAGNOSTIC] {self._provider} first message RECEIVED",
                file=sys.stderr,
                flush=True,
            )
        return result

    def close(self, *args: object, **kwargs: object) -> object:
        return self._websocket.close(*args, **kwargs)


_diagnostic_context = threading.local()


def _diagnostic_decode(provider: str, original_decode):
    def decode(*args: object, **kwargs: object):
        print(
            f"[DIAGNOSTIC] {provider} decode starting",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = original_decode(*args, **kwargs)
        except Exception as error:
            _report_failure(provider, "decode", error)
            raise
        print(
            f"[DIAGNOSTIC] {provider} decode SUCCESS",
            file=sys.stderr,
            flush=True,
        )
        return result

    return staticmethod(decode)


def _diagnostic_stream(provider: str, original_stream):
    def stream(self, *args: object, **kwargs: object):
        try:
            for update in original_stream(self, *args, **kwargs):
                previous_provider = getattr(
                    _diagnostic_context, "provider", None
                )
                _diagnostic_context.provider = provider
                print(
                    f"[DIAGNOSTIC] {provider} PriceUpdate emit starting",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    yield update
                    print(
                        f"[DIAGNOSTIC] {provider} PriceUpdate emit complete",
                        file=sys.stderr,
                        flush=True,
                    )
                finally:
                    _diagnostic_context.provider = previous_provider
        except Exception as error:
            _report_failure(provider, "feed stream", error)
            raise

    return stream


_connection_manager_init = ConnectionManager.__init__


def _diagnostic_connection_manager_init(self, *args: object, **kwargs: object):
    _connection_manager_init(self, *args, **kwargs)
    original_callback = self._on_update

    def diagnostic_callback(update) -> None:
        provider = getattr(_diagnostic_context, "provider", "unknown")
        print(
            f"[DIAGNOSTIC] {provider} PriceUpdate callback starting",
            file=sys.stderr,
            flush=True,
        )
        try:
            original_callback(update)
        except Exception as error:
            _report_failure(provider, "PriceUpdate callback", error)
            raise
        print(
            f"[DIAGNOSTIC] {provider} PriceUpdate callback complete",
            file=sys.stderr,
            flush=True,
        )

    self._on_update = diagnostic_callback


ConnectionManager.__init__ = _diagnostic_connection_manager_init
DeltaPriceFeed._decode = _diagnostic_decode(
    "Delta", DeltaPriceFeed._decode
)
UpstoxPriceFeed._decode = _diagnostic_decode(
    "Upstox", UpstoxPriceFeed._decode
)
DeltaPriceFeed.stream = _diagnostic_stream(
    "Delta", DeltaPriceFeed.stream
)
UpstoxPriceFeed.stream = _diagnostic_stream(
    "Upstox", UpstoxPriceFeed.stream
)


def _diagnostic_get_websocket_connect(feed, original_getter, provider: str):
    try:
        connect = original_getter(feed)
    except Exception as error:
        _report_failure(provider, "websocket connect", error)
        raise

    def log_connect_failure(*args: object, **kwargs: object) -> object:
        timeout_seconds = 10
        endpoint = args[0] if args and isinstance(args[0], str) else None
        if endpoint:
            try:
                parsed = urlsplit(endpoint)
                hostname = parsed.hostname
                if (
                    parsed.scheme in {"ws", "wss"}
                    and hostname
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                    and not re.search(
                        r"(token|secret|key|credential|auth)",
                        hostname,
                        flags=re.IGNORECASE,
                    )
                ):
                    print(
                        f"[DIAGNOSTIC] {provider} websocket endpoint hostname: "
                        f"{hostname}",
                        file=sys.stderr,
                        flush=True,
                    )
            except ValueError:
                pass

        print(
            f"[DIAGNOSTIC] {provider} websocket connect starting",
            file=sys.stderr,
            flush=True,
        )
        completed = threading.Event()
        lock = threading.Lock()
        state: dict[str, object] = {"timed_out": False}

        def run_connect() -> None:
            try:
                result = connect(*args, **kwargs)
            except Exception as error:
                with lock:
                    state["error"] = error
                    state["done"] = True
                    timed_out = state["timed_out"]
                    completed.set()
                if timed_out:
                    return
                return

            with lock:
                state["result"] = _DiagnosticWebSocket(result, provider)
                state["done"] = True
                timed_out = state["timed_out"]
                completed.set()
            if not timed_out:
                print(
                    f"[DIAGNOSTIC] {provider} websocket CONNECTED",
                    file=sys.stderr,
                    flush=True,
                )
            if timed_out:
                close = getattr(result, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

        threading.Thread(
            target=run_connect,
            name=f"diagnostic-{provider.lower()}-websocket-connect",
            daemon=True,
        ).start()

        if not completed.wait(timeout_seconds):
            with lock:
                if not state.get("done"):
                    state["timed_out"] = True
                    timed_out = True
                else:
                    timed_out = False
            if timed_out:
                print(
                    f"[DIAGNOSTIC] {provider} websocket connect TIMEOUT "
                    f"after {timeout_seconds} seconds",
                    file=sys.stderr,
                    flush=True,
                )
                raise TimeoutError(
                    f"{provider} websocket connect diagnostic timeout"
                )

        with lock:
            error = state.get("error")
            result = state.get("result")
        if isinstance(error, Exception):
            _report_failure(provider, "websocket connect", error)
            raise error
        print(
            f"[DIAGNOSTIC] {provider} websocket connect SUCCESS",
            file=sys.stderr,
            flush=True,
        )
        return result

    return log_connect_failure


_delta_get_websocket_connect = DeltaPriceFeed._get_websocket_connect
_upstox_get_websocket_connect = UpstoxPriceFeed._get_websocket_connect


def _diagnostic_delta_get_websocket_connect(self: DeltaPriceFeed):
    return _diagnostic_get_websocket_connect(
        self, _delta_get_websocket_connect, "Delta"
    )


def _diagnostic_upstox_get_websocket_connect(self: UpstoxPriceFeed):
    return _diagnostic_get_websocket_connect(
        self, _upstox_get_websocket_connect, "Upstox"
    )


DeltaPriceFeed._get_websocket_connect = _diagnostic_delta_get_websocket_connect
UpstoxPriceFeed._get_websocket_connect = _diagnostic_upstox_get_websocket_connect
