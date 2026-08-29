import streamlit as st

from lib import auth
from lib.api_client import ApiError, client

st.title("🏠 Dashboard")

user = auth.current_user()
if user is None:  # token was rejected during a previous call
    st.rerun()

left, middle, right = st.columns(3)
left.metric("Signed in as", user["username"])
middle.metric("Role", user["role"])
right.metric("User ID", user["id"])

st.divider()
st.subheader("Backend status")

try:
    health = client.get("/health")
except ApiError as exc:
    st.error(str(exc))
else:
    if health.ok and health.data.get("status") == "ok":
        st.success("Backend and database are healthy.")
    else:
        st.warning(f"Backend reported a problem (HTTP {health.status_code}).")

    columns = st.columns(3)
    columns[0].metric("Backend", health.data.get("status", "unknown"))
    columns[1].metric("Database", health.data.get("database", "unknown"))
    columns[2].metric("API version", health.data.get("version", "unknown"))

st.divider()
st.caption(
    "Records, Analytics and Realtime pages are added in the following steps."
)
