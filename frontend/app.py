import streamlit as st

from lib import auth, realtime_state
from lib.config import API_BASE_URL

st.set_page_config(page_title="Realtime Monitoring System", page_icon="📈", layout="wide")


def render_login() -> None:
    st.title("📈 Realtime Monitoring System")
    st.caption("Sign in to continue.")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        error = auth.login(username, password)
        if error:
            st.error(error)
        else:
            st.rerun()

    st.caption(f"Backend: `{API_BASE_URL}`")


def render_account_sidebar() -> None:
    user = auth.current_user()
    with st.sidebar:
        st.divider()
        st.markdown(f"**{user['username']}**")
        st.caption(f"Role: {user['role']}")
        if st.button("Log out", use_container_width=True):
            # Stop the realtime receiver first: it must not keep using the
            # token of a session that has ended.
            realtime_state.shutdown_session()
            auth.logout()
            st.rerun()


def render_app() -> None:
    # Every role may reach every page here; write controls are restricted
    # per page in later steps, and the backend enforces the real rules.
    navigation = st.navigation(
        [
            st.Page("views/home.py", title="Home", icon="🏠", default=True),
            st.Page("views/records.py", title="Records", icon="🗂️"),
            st.Page("views/analytics.py", title="Analytics", icon="📊"),
            st.Page("views/realtime.py", title="Realtime", icon="📡"),
        ]
    )
    render_account_sidebar()
    navigation.run()


if auth.is_authenticated():
    render_app()
else:
    render_login()
