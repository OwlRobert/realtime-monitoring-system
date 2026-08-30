"""Streamlit-session glue for the realtime receiver.

Kept apart from `lib.realtime` so the receiver logic stays testable without a
Streamlit runtime. One session holds at most one receiver thread.
"""

import streamlit as st

from lib.realtime import RealtimeSession

SESSION_KEY = "realtime_session"


def get_session(token: str) -> RealtimeSession:
    """Return this browser session's receiver, creating it once.

    If the token changed (re-login), the old receiver is stopped so a stale
    JWT is never used.
    """
    session = st.session_state.get(SESSION_KEY)
    if session is not None and session.token != token:
        session.shutdown()
        session = None
    if session is None:
        session = RealtimeSession(token)
        st.session_state[SESSION_KEY] = session
    return session


def shutdown_session() -> None:
    """Stop and forget the receiver; called on logout."""
    session = st.session_state.pop(SESSION_KEY, None)
    if session is not None:
        session.shutdown()
