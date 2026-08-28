import streamlit as st

from lib.api_client import ApiError, health
from lib.config import API_BASE_URL

st.set_page_config(page_title="Realtime Monitoring System", page_icon="📈", layout="wide")

st.title("📈 Realtime Monitoring System")
st.caption("Phase 1 — service skeleton. Authentication, data and realtime pages follow.")

st.subheader("System status")

col_status, col_action = st.columns([3, 1])
with col_action:
    st.button("Refresh", use_container_width=True)

with col_status:
    try:
        status_code, payload = health()
    except ApiError as exc:
        st.error(f"Backend unavailable\n\n{exc}")
    else:
        if status_code == 200 and payload.get("status") == "ok":
            st.success("Backend and database are healthy.")
        else:
            st.warning(f"Backend reported a problem (HTTP {status_code}).")

        left, middle, right = st.columns(3)
        left.metric("Backend", payload.get("status", "unknown"))
        middle.metric("Database", payload.get("database", "unknown"))
        right.metric("API version", payload.get("version", "unknown"))

st.divider()
st.caption(f"API base URL: `{API_BASE_URL}`")
