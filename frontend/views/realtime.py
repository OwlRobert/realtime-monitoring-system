import pandas as pd
import streamlit as st

from lib import auth, realtime, realtime_state, records_api, ui
from lib.api_client import ApiError

st.title("📡 Realtime Monitoring")
st.caption(
    "Live readings pushed by the backend over WebSocket. "
    "Every role may watch the stream."
)

token = auth.token()
if token is None:
    st.rerun()

session = realtime_state.get_session(token)
client = session.client

# --------------------------------------------------------------------------
# Lifecycle controls (main thread only)
# --------------------------------------------------------------------------

start, stop, clear = st.columns([1, 1, 1])
if start.button(
    "▶ Connect", use_container_width=True, disabled=client.is_running, type="primary"
):
    client.start()
    st.rerun()
if stop.button("⏹ Disconnect", use_container_width=True, disabled=not client.is_running):
    session.shutdown()
    st.rerun()
if clear.button("Clear history", use_container_width=True):
    session.history.clear()
    st.rerun()

status = client.status
if status == realtime.CONNECTED:
    st.success(realtime.status_message(client))
elif status == realtime.ERROR:
    st.error(realtime.status_message(client))
    st.caption("Disconnect and connect again to retry, or sign in again if your session expired.")
elif status == realtime.CONNECTING:
    st.info(realtime.status_message(client))
else:
    st.info("Disconnected — press Connect to start streaming.")


# --------------------------------------------------------------------------
# Live area: re-runs on its own timer, leaving the rest of the page alone
# --------------------------------------------------------------------------


@st.fragment(run_every="1s")
def live_view() -> None:
    session.pump()  # drain the queue filled by the receiver thread
    history = session.history
    readings = history.readings
    latest = history.latest

    columns = st.columns(4)
    columns[0].metric("Connection", client.status.title())
    columns[1].metric(
        "Latest value",
        f"{latest['value']:.2f}" if latest else "—",
        delta="⚠️ anomaly" if latest and latest["is_anomaly"] else None,
        delta_color="inverse",
    )
    columns[2].metric("Latest category", latest["category"] if latest else "—")
    columns[3].metric(
        "Anomalies in window", f"{history.anomaly_count} / {len(history)}"
    )

    if latest:
        st.caption(f"Last reading at {latest['timestamp']} · showing the most recent {history.maxlen} readings")
    if client.dropped or client.malformed:
        st.caption(f"Skipped {client.malformed} malformed and {client.dropped} overflowed messages.")

    if not readings:
        st.info("No readings yet. Connect to start receiving the live stream.")
        return

    frame = pd.DataFrame(readings)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])

    st.subheader("Live values")
    st.caption("Value over time, one line per category; anomalies are drawn as a separate series.")
    wide = frame.pivot_table(
        index="timestamp", columns="category", values="value", aggfunc="last"
    )
    anomalies = frame[frame["is_anomaly"]]
    if not anomalies.empty:
        wide["⚠️ anomaly"] = anomalies.set_index("timestamp")["value"]
    st.line_chart(wide)

    st.subheader("Current window by category")
    st.caption("Average value per category across the readings currently displayed.")
    averages = history.average_by_category()
    counts = history.count_by_category()
    bars = pd.DataFrame(
        {"average": pd.Series(averages), "readings": pd.Series(counts)}
    )
    left, right = st.columns([2, 1])
    left.bar_chart(bars["average"])
    right.dataframe(bars, use_container_width=True)

    st.subheader("Recent anomalies")
    flagged = history.anomalies[-10:]
    if not flagged:
        st.caption("No anomalies in the current window.")
    else:
        table = pd.DataFrame(flagged)[["timestamp", "category", "value", "title"]]
        st.dataframe(table.iloc[::-1], use_container_width=True, hide_index=True)


live_view()

# --------------------------------------------------------------------------
# Persisted history (secondary)
# --------------------------------------------------------------------------

st.divider()
with st.expander("Persisted realtime history (from MariaDB)"):
    st.caption("The last rows written by the backend's batch persistence.")
    try:
        result = records_api.list_records(
            source="REALTIME", page_size=10, sort_by="id", order="desc"
        )
    except ApiError as exc:
        st.error(str(exc))
    else:
        if not result.ok:
            ui.report_error(result)
        else:
            st.caption(f"{result.data['total']:,} realtime rows persisted in total.")
            stored = pd.DataFrame(result.data["items"])
            if not stored.empty:
                st.dataframe(
                    stored[["id", "timestamp", "category", "value", "is_anomaly"]],
                    use_container_width=True,
                    hide_index=True,
                )
