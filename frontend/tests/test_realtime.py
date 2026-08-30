import json
import threading
import time

import pytest

from lib import realtime
from lib.realtime import History, RealtimeClient, build_ws_url, parse_reading, redact

TOKEN = "header.payload.signature"

READING = {
    "title": "cpu reading",
    "value": 42.5,
    "category": "cpu",
    "timestamp": "2026-08-30T12:00:00",
    "source": "REALTIME",
    "is_anomaly": False,
}


def reading(value=1.0, category="cpu", *, anomaly=False):
    return {**READING, "value": value, "category": category, "is_anomaly": anomaly}


class FakeSocket:
    """Stands in for a websocket connection in the receiver thread."""

    def __init__(self, messages, *, block=True, raise_on_connect=None):
        self.messages = list(messages)
        self.block = block
        self.closed = False
        self.raise_on_connect = raise_on_connect

    def __enter__(self):
        if self.raise_on_connect:
            raise self.raise_on_connect
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def recv(self, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        if self.closed or not self.block:
            raise TimeoutError
        time.sleep(0.01)
        raise TimeoutError

    def close(self):
        self.closed = True


def factory(socket):
    def connect(url):
        connect.url = url
        return socket

    connect.url = None
    return connect


def wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while not predicate():
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


# --------------------------------------------------------------------------
# URL construction
# --------------------------------------------------------------------------


def test_http_base_url_becomes_ws():
    assert build_ws_url(TOKEN, "http://backend:8000") == (
        f"ws://backend:8000/ws/realtime?token={TOKEN}"
    )


def test_https_base_url_becomes_wss():
    assert build_ws_url(TOKEN, "https://api.example.com").startswith("wss://api.example.com/ws/realtime")


def test_url_is_built_from_configuration_not_localhost():
    """Inside Compose the backend is reached by service name."""
    url = build_ws_url(TOKEN, "http://backend:8000")

    assert "backend:8000" in url
    assert "localhost" not in url


def test_token_travels_as_a_query_parameter():
    assert build_ws_url(TOKEN, "http://backend:8000").endswith(f"?token={TOKEN}")


# --------------------------------------------------------------------------
# Token safety
# --------------------------------------------------------------------------


def test_redact_removes_the_token():
    message = f"failed to connect to ws://backend:8000/ws/realtime?token={TOKEN}"

    assert TOKEN not in redact(message, TOKEN)
    assert "***" in redact(message, TOKEN)


def test_status_message_never_contains_the_token():
    client = RealtimeClient(TOKEN, connect=factory(FakeSocket([], raise_on_connect=RuntimeError(
        f"refused for ws://backend:8000/ws/realtime?token={TOKEN}"
    ))))
    client.start()
    wait_until(lambda: client.status == realtime.ERROR)

    assert TOKEN not in realtime.status_message(client)
    assert TOKEN not in (client.error or "")
    client.stop()


# --------------------------------------------------------------------------
# Message parsing
# --------------------------------------------------------------------------


def test_valid_json_message_is_parsed():
    parsed = parse_reading(json.dumps(READING))

    assert parsed["value"] == 42.5
    assert parsed["category"] == "cpu"
    assert parsed["is_anomaly"] is False
    assert parsed["source"] == "REALTIME"


def test_bytes_messages_are_accepted():
    assert parse_reading(json.dumps(READING).encode())["value"] == 42.5


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "",
        "[1, 2, 3]",
        json.dumps({"value": 1.0}),                       # missing fields
        json.dumps({**READING, "value": "abc"}),          # unusable value
        None,
        123,
        b"\xff\xfe",
    ],
)
def test_malformed_messages_are_skipped(raw):
    assert parse_reading(raw) is None


def test_a_malformed_message_does_not_stop_the_stream():
    socket = FakeSocket(["garbage", json.dumps(READING)])
    client = RealtimeClient(TOKEN, connect=factory(socket))
    client.start()
    wait_until(lambda: len(client.drain()) == 0 and client.malformed == 1, timeout=3)
    client.stop()

    assert client.malformed == 1


# --------------------------------------------------------------------------
# Bounded history
# --------------------------------------------------------------------------


def test_history_is_bounded():
    history = History(maxlen=5)
    history.extend(reading(float(index)) for index in range(20))

    assert len(history) == 5
    assert [r["value"] for r in history.readings] == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_default_history_bound():
    assert History().maxlen == realtime.MAX_HISTORY == 100


def test_latest_is_the_most_recent_reading():
    history = History()
    history.extend([reading(1.0), reading(2.0)])

    assert history.latest["value"] == 2.0


def test_empty_history_has_no_latest():
    assert History().latest is None


def test_clear_empties_the_window():
    history = History()
    history.extend([reading(1.0)])
    history.clear()

    assert len(history) == 0


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def test_anomaly_count_uses_the_backend_flag():
    history = History()
    history.extend([reading(1.0), reading(90.0, anomaly=True), reading(95.0, anomaly=True)])

    assert history.anomaly_count == 2
    assert [r["value"] for r in history.anomalies] == [90.0, 95.0]


def test_anomalies_are_not_recomputed_from_the_value():
    """A high value is not an anomaly unless the backend said so."""
    history = History()
    history.extend([reading(99.9, anomaly=False)])

    assert history.anomaly_count == 0


def test_average_by_category():
    history = History()
    history.extend([
        reading(10.0, "cpu"),
        reading(20.0, "cpu"),
        reading(60.0, "memory"),
    ])

    assert history.average_by_category() == {"cpu": 15.0, "memory": 60.0}


def test_count_by_category():
    history = History()
    history.extend([reading(1.0, "cpu"), reading(2.0, "cpu"), reading(3.0, "memory")])

    assert history.count_by_category() == {"cpu": 2, "memory": 1}


def test_aggregations_of_an_empty_window():
    history = History()

    assert history.average_by_category() == {}
    assert history.count_by_category() == {}
    assert history.anomaly_count == 0


# --------------------------------------------------------------------------
# Receiver lifecycle
# --------------------------------------------------------------------------


def test_readings_reach_the_queue():
    socket = FakeSocket([json.dumps(READING), json.dumps(reading(7.0))])
    client = RealtimeClient(TOKEN, connect=factory(socket))
    client.start()
    wait_until(lambda: client.status == realtime.CONNECTED)

    collected = []
    wait_until(lambda: collected.extend(client.drain()) or len(collected) >= 2)
    client.stop()

    assert [r["value"] for r in collected] == [42.5, 7.0]


def test_start_does_not_create_a_second_receiver():
    socket = FakeSocket([])
    client = RealtimeClient(TOKEN, connect=factory(socket))
    before = threading.active_count()

    assert client.start() is True
    wait_until(lambda: client.status == realtime.CONNECTED)
    assert client.start() is False          # already running
    assert client.start() is False
    assert threading.active_count() == before + 1

    client.stop()


def test_stop_ends_the_thread_and_resets_state():
    client = RealtimeClient(TOKEN, connect=factory(FakeSocket([])))
    client.start()
    wait_until(lambda: client.status == realtime.CONNECTED)

    client.stop()

    assert client.is_running is False
    assert client.status == realtime.DISCONNECTED


def test_restart_after_stop_is_allowed():
    client = RealtimeClient(TOKEN, connect=factory(FakeSocket([])))
    client.start()
    wait_until(lambda: client.status == realtime.CONNECTED)
    client.stop()

    assert client.start() is True
    wait_until(lambda: client.status == realtime.CONNECTED)
    client.stop()


def test_connection_failure_is_reported_as_error_state():
    client = RealtimeClient(
        TOKEN, connect=factory(FakeSocket([], raise_on_connect=OSError("refused")))
    )
    client.start()
    wait_until(lambda: client.status == realtime.ERROR)

    assert "refused" in (client.error or "")
    assert client.is_running is False
    client.stop()


def test_queue_overflow_is_counted_not_unbounded():
    messages = [json.dumps(reading(float(i))) for i in range(10)]
    client = RealtimeClient(TOKEN, connect=factory(FakeSocket(messages)), max_queue=3)
    client.start()
    wait_until(lambda: client.dropped >= 1, timeout=3)
    client.stop()

    assert client.dropped >= 1
    assert len(client.drain()) <= 3


def test_session_pump_moves_queue_into_bounded_history():
    socket = FakeSocket([json.dumps(reading(float(i))) for i in range(5)])
    session = realtime.RealtimeSession(TOKEN, connect=factory(socket))
    session.client.start()
    wait_until(lambda: session.pump() and len(session.history) >= 5, timeout=3)
    session.shutdown()

    assert len(session.history) == 5
    assert session.status == realtime.DISCONNECTED


def test_session_shutdown_stops_the_receiver():
    session = realtime.RealtimeSession(TOKEN, connect=factory(FakeSocket([])))
    session.client.start()
    wait_until(lambda: session.status == realtime.CONNECTED)

    session.shutdown()

    assert session.client.is_running is False


# --------------------------------------------------------------------------
# Idle shutdown (a closed browser tab must not leave a thread running)
# --------------------------------------------------------------------------


def test_receiver_stops_itself_when_nobody_drains():
    """Streamlit offers no session-close hook, so the receiver self-terminates."""
    client = RealtimeClient(TOKEN, connect=factory(FakeSocket([])), idle_timeout=0.2)
    client.start()
    wait_until(lambda: client.status == realtime.CONNECTED)

    wait_until(lambda: not client.is_running, timeout=5)

    assert client.stopped_idle is True
    assert client.status == realtime.DISCONNECTED


def test_draining_keeps_the_receiver_alive():
    client = RealtimeClient(TOKEN, connect=factory(FakeSocket([])), idle_timeout=0.4)
    client.start()
    wait_until(lambda: client.status == realtime.CONNECTED)

    for _ in range(6):          # a page that keeps polling stays connected
        time.sleep(0.1)
        client.drain()

    assert client.is_running is True
    assert client.stopped_idle is False
    client.stop()


def test_default_idle_timeout_is_generous_relative_to_the_refresh_rate():
    assert realtime.IDLE_TIMEOUT_SECONDS >= 10
