"""WebSocket receiver for the backend's realtime stream.

Threading contract
------------------
The receiver runs in one background thread per Streamlit session. That thread
NEVER calls a `st.*` API: it only connects, parses messages, pushes them onto a
thread-safe queue and records a small status snapshot behind a lock. The
Streamlit script thread drains the queue and does all rendering.

The connection is made by the Streamlit *server* process, not the browser, so
the container-internal backend URL is the right one to use.
"""

import json
import logging
import threading
import time
from collections import deque
from queue import Empty, Full, Queue
from typing import Any, Callable, Iterable
from urllib.parse import urlparse, urlunparse

from websockets.sync.client import connect as ws_connect

from lib.config import API_BASE_URL

logger = logging.getLogger(__name__)

WS_PATH = "/ws/realtime"
# One reading per second, so 100 readings is a little over a minute and a half
# of live history: enough to see a trend, small enough to stay cheap to render.
MAX_HISTORY = 100
MAX_QUEUE = 1000
RECV_TIMEOUT_SECONDS = 1.0
# Streamlit gives no hook for "the browser tab closed", so the receiver stops
# itself once nobody has drained its queue for a while. The page drains once a
# second, so this only fires when the session is really gone.
IDLE_TIMEOUT_SECONDS = 30.0

REQUIRED_FIELDS = ("title", "value", "category", "timestamp", "source", "is_anomaly")

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"
ERROR = "error"


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def build_ws_url(token: str, base_url: str = API_BASE_URL) -> str:
    """Turn the HTTP backend URL into the authenticated WebSocket URL.

    The backend authenticates the handshake with a token query parameter.
    """
    parts = urlparse(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunparse((scheme, parts.netloc, WS_PATH, "", f"token={token}", ""))


def redact(text: str, token: str | None) -> str:
    """Remove the token from anything that might be shown or logged."""
    if not token:
        return text
    return text.replace(token, "***")


def parse_reading(raw: Any) -> dict[str, Any] | None:
    """Decode one message, or None when it is not a usable reading.

    A malformed message is skipped rather than allowed to break the page.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    if any(field not in raw for field in REQUIRED_FIELDS):
        return None
    try:
        value = float(raw["value"])
    except (TypeError, ValueError):
        return None

    return {
        "title": str(raw["title"]),
        "value": value,
        "category": str(raw["category"]),
        "timestamp": str(raw["timestamp"]),
        "source": str(raw["source"]),
        "is_anomaly": bool(raw["is_anomaly"]),
    }


class History:
    """Bounded, in-memory view history. MariaDB keeps the real history."""

    def __init__(self, maxlen: int = MAX_HISTORY) -> None:
        self._readings: deque[dict[str, Any]] = deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int:
        return self._readings.maxlen or 0

    def __len__(self) -> int:
        return len(self._readings)

    def extend(self, readings: Iterable[dict[str, Any]]) -> None:
        self._readings.extend(readings)

    def clear(self) -> None:
        self._readings.clear()

    @property
    def readings(self) -> list[dict[str, Any]]:
        return list(self._readings)

    @property
    def latest(self) -> dict[str, Any] | None:
        return self._readings[-1] if self._readings else None

    @property
    def anomalies(self) -> list[dict[str, Any]]:
        return [r for r in self._readings if r["is_anomaly"]]

    @property
    def anomaly_count(self) -> int:
        return sum(1 for r in self._readings if r["is_anomaly"])

    def average_by_category(self) -> dict[str, float]:
        """Mean value per category across the current window."""
        totals: dict[str, list[float]] = {}
        for reading in self._readings:
            totals.setdefault(reading["category"], []).append(reading["value"])
        return {
            category: sum(values) / len(values)
            for category, values in sorted(totals.items())
        }

    def count_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for reading in self._readings:
            counts[reading["category"]] = counts.get(reading["category"], 0) + 1
        return dict(sorted(counts.items()))


# --------------------------------------------------------------------------
# Receiver
# --------------------------------------------------------------------------


class RealtimeClient:
    """Owns one background receiver thread and a queue of readings."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_BASE_URL,
        connect: Callable[[str], Any] | None = None,
        max_queue: int = MAX_QUEUE,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self._token = token
        self._url = build_ws_url(token, base_url)
        self._connect = connect or ws_connect
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._status = DISCONNECTED
        self._error: str | None = None
        self._socket: Any = None
        self.idle_timeout = idle_timeout
        self._last_drain = time.monotonic()
        self.dropped = 0
        self.malformed = 0
        self.stopped_idle = False

    # -- state, readable from the Streamlit thread ------------------------

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _set(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self._status = status
            self._error = error

    # -- lifecycle --------------------------------------------------------

    def start(self) -> bool:
        """Start the receiver. Returns False if one is already running."""
        if self.is_running:
            return False
        self._stopping.clear()
        self._last_drain = time.monotonic()
        self.stopped_idle = False
        self._set(CONNECTING)
        self._thread = threading.Thread(
            target=self._run, name="realtime-receiver", daemon=True
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        """Ask the receiver to finish and wait briefly for the thread to end."""
        self._stopping.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:  # noqa: BLE001 - already closing
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        if self.status != ERROR:
            self._set(DISCONNECTED)

    # -- background thread ------------------------------------------------

    def _run(self) -> None:
        try:
            with self._connect(self._url) as socket:
                self._socket = socket
                self._set(CONNECTED)
                while not self._stopping.is_set():
                    if time.monotonic() - self._last_drain > self.idle_timeout:
                        # The Streamlit session stopped consuming: it is gone.
                        self.stopped_idle = True
                        logger.info("Realtime receiver idle; closing connection")
                        break
                    try:
                        raw = socket.recv(timeout=RECV_TIMEOUT_SECONDS)
                    except TimeoutError:
                        continue
                    reading = parse_reading(raw)
                    if reading is None:
                        self.malformed += 1
                        continue
                    try:
                        self._queue.put_nowait(reading)
                    except Full:
                        self.dropped += 1
        except Exception as exc:  # noqa: BLE001 - surfaced as connection state
            if self._stopping.is_set():
                self._set(DISCONNECTED)
            else:
                message = redact(f"{type(exc).__name__}: {exc}", self._token)
                logger.warning("Realtime receiver stopped: %s", message)
                self._set(ERROR, message)
        else:
            self._set(DISCONNECTED)
        finally:
            self._socket = None

    # -- consumed by the Streamlit thread ---------------------------------

    def drain(self, limit: int = MAX_QUEUE) -> list[dict[str, Any]]:
        """Take everything waiting on the queue; also marks the session alive."""
        self._last_drain = time.monotonic()
        readings: list[dict[str, Any]] = []
        for _ in range(limit):
            try:
                readings.append(self._queue.get_nowait())
            except Empty:
                break
        return readings


class RealtimeSession:
    """One client plus its history, kept in st.session_state."""

    def __init__(self, token: str, **kwargs: Any) -> None:
        self.token = token
        self.client = RealtimeClient(token, **kwargs)
        self.history = History()

    @property
    def status(self) -> str:
        return self.client.status

    def pump(self) -> int:
        """Move queued readings into the bounded history."""
        readings = self.client.drain()
        self.history.extend(readings)
        return len(readings)

    def shutdown(self) -> None:
        self.client.stop()


def status_message(client: RealtimeClient) -> str:
    """Human-readable connection state, never containing the token."""
    status = client.status
    if status == ERROR:
        return f"Connection failed — {client.error or 'unknown error'}"
    return {
        DISCONNECTED: "Disconnected",
        CONNECTING: "Connecting…",
        CONNECTED: "Connected",
    }.get(status, status)
