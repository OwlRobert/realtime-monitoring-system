"""Authentication state, held only in st.session_state.

The token lives for the active browser session; there is no persistence
across sessions and no refresh token.
"""

from typing import Any

import streamlit as st

from lib.api_client import ApiError, ApiResult, client

TOKEN_KEY = "access_token"
USER_KEY = "current_user"

INVALID_CREDENTIALS = "Incorrect username or password."


def token() -> str | None:
    return st.session_state.get(TOKEN_KEY)


def current_user() -> dict[str, Any] | None:
    return st.session_state.get(USER_KEY)


def is_authenticated() -> bool:
    return token() is not None and current_user() is not None


def role() -> str | None:
    user = current_user()
    return user.get("role") if user else None


def can_write() -> bool:
    """Viewers are read-only. The backend remains the source of truth."""
    return role() in {"ADMIN", "USER"}


def is_admin() -> bool:
    return role() == "ADMIN"


def login(username: str, password: str) -> str | None:
    """Log in and load the current user. Returns an error message, or None."""
    if not username or not password:
        return "Enter both a username and a password."

    try:
        result = client.post(
            "/auth/login", json={"username": username, "password": password}
        )
    except ApiError as exc:
        return str(exc)

    if result.unauthorized:
        return INVALID_CREDENTIALS
    if not result.ok:
        return result.error_message

    access_token = (result.data or {}).get("access_token")
    if not access_token:
        return "Backend did not return an access token."

    try:
        me = client.get("/auth/me", token=access_token)
    except ApiError as exc:
        return str(exc)

    if not me.ok:
        return me.error_message

    # Only safe fields are kept; the password is never stored anywhere.
    st.session_state[TOKEN_KEY] = access_token
    st.session_state[USER_KEY] = me.data
    return None


def logout() -> None:
    st.session_state.pop(TOKEN_KEY, None)
    st.session_state.pop(USER_KEY, None)


def request(method: str, path: str, **kwargs: Any) -> ApiResult:
    """Authenticated request that drops the session on a 401.

    A rejected token means the session is over: the caller re-runs and the
    login form appears.
    """
    result = client.request(method, path, token=token(), **kwargs)
    if result.unauthorized:
        logout()
    return result


def get(path: str, **kwargs: Any) -> ApiResult:
    return request("GET", path, **kwargs)
