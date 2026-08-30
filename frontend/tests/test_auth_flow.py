"""Login/logout behaviour driven through Streamlit's AppTest.

Only the auth flow is exercised; rendering internals are not.
"""

from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from lib.api_client import ApiError, ApiResult

USER = {
    "id": 7,
    "username": "alice",
    "email": "alice@example.com",
    "role": "USER",
    "is_active": True,
    "created_at": "2026-08-29T12:00:00",
}
TOKEN = {"access_token": "a-token", "token_type": "bearer"}


def responses(*results):
    """Return the given results in order, one per client call."""
    queue = list(results)
    return lambda *args, **kwargs: queue.pop(0)


def run_login(app: AppTest, username="alice", password="a-strong-password"):
    app.text_input[0].set_value(username)
    app.text_input[1].set_value(password)
    app.button[0].click().run()
    return app


@pytest.fixture
def app():
    return AppTest.from_file("app.py", default_timeout=30)


def test_login_form_is_shown_when_signed_out(app):
    app.run()

    assert app.title[0].value.endswith("Realtime Monitoring System")
    assert len(app.text_input) == 2
    assert "access_token" not in app.session_state


def test_successful_login_stores_token_and_user(app):
    app.run()

    with patch("lib.api_client.ApiClient.request") as request:
        request.side_effect = responses(ApiResult(200, TOKEN), ApiResult(200, USER))
        run_login(app)

    assert app.session_state["access_token"] == "a-token"
    assert app.session_state["current_user"]["username"] == "alice"
    assert not app.exception


def test_password_is_never_stored_in_session_state(app):
    app.run()

    with patch("lib.api_client.ApiClient.request") as request:
        request.side_effect = responses(ApiResult(200, TOKEN), ApiResult(200, USER))
        run_login(app, password="super-secret")

    # Streamlit keeps widget values in its own internal state; what matters
    # is that the app never puts the password in the state it controls.
    assert "super-secret" not in str(app.session_state["current_user"])
    assert "super-secret" not in app.session_state["access_token"]


def test_invalid_credentials_show_an_error(app):
    app.run()

    with patch("lib.api_client.ApiClient.request") as request:
        request.side_effect = responses(
            ApiResult(401, {"detail": "Incorrect username or password"})
        )
        run_login(app, password="wrong")

    assert app.error[0].value == "Incorrect username or password."
    assert "access_token" not in app.session_state


def test_unreachable_backend_shows_an_error(app):
    app.run()

    with patch("lib.api_client.ApiClient.request") as request:
        request.side_effect = ApiError("Cannot reach the backend at http://backend:8000")
        run_login(app)

    assert "Cannot reach the backend" in app.error[0].value
    assert "access_token" not in app.session_state


def test_empty_credentials_are_rejected_without_calling_the_backend(app):
    app.run()

    with patch("lib.api_client.ApiClient.request") as request:
        run_login(app, username="", password="")

    request.assert_not_called()
    assert app.error[0].value == "Enter both a username and a password."


def test_authenticated_shell_shows_username_and_role(app):
    app.session_state["access_token"] = "a-token"
    app.session_state["current_user"] = USER

    with patch("lib.api_client.ApiClient.request") as request:
        request.return_value = ApiResult(
            200, {"status": "ok", "database": "ok", "version": "0.1.0"}
        )
        app.run()

    sidebar_text = " ".join(element.value for element in app.sidebar.markdown)
    sidebar_text += " ".join(element.value for element in app.sidebar.caption)
    assert "alice" in sidebar_text
    assert "USER" in sidebar_text
    assert not app.exception


def test_logout_clears_the_session(app):
    app.session_state["access_token"] = "a-token"
    app.session_state["current_user"] = USER

    with patch("lib.api_client.ApiClient.request") as request:
        request.return_value = ApiResult(
            200, {"status": "ok", "database": "ok", "version": "0.1.0"}
        )
        app.run()
        app.sidebar.button[0].click().run()

    assert "access_token" not in app.session_state
    assert "current_user" not in app.session_state
    assert len(app.text_input) == 2  # back at the login form


# --------------------------------------------------------------------------
# Admin navigation visibility (UX only; FastAPI is the security boundary)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "is_admin", "can_write"),
    [("ADMIN", True, True), ("USER", False, True), ("VIEWER", False, False)],
)
def test_role_helpers_drive_navigation_and_controls(app, role, is_admin, can_write):
    """`auth.is_admin` decides whether the Admin page is listed at all."""
    from lib import auth

    app.session_state["access_token"] = "a-token"
    app.session_state["current_user"] = {**USER, "role": role}

    with patch("lib.api_client.ApiClient.request") as request:
        request.return_value = ApiResult(
            200, {"status": "ok", "database": "ok", "version": "0.1.0"}
        )
        app.run()

    with patch.object(auth.st, "session_state", {"current_user": {"role": role}}):
        assert auth.is_admin() is is_admin
        assert auth.can_write() is can_write
    assert not app.exception


def test_signed_out_session_is_not_an_admin():
    from lib import auth

    with patch.object(auth.st, "session_state", {}):
        assert auth.is_admin() is False
        assert auth.can_write() is False


# --------------------------------------------------------------------------
# Signed-out UI must not keep the authenticated navigation
# --------------------------------------------------------------------------


def sidebar_text(app) -> str:
    parts = [element.value for element in app.sidebar.markdown]
    parts += [element.value for element in app.sidebar.caption]
    parts += [button.label for button in app.sidebar.button]
    return " ".join(parts)


def test_signed_out_app_shows_only_the_login_form(app):
    app.run()

    assert len(app.text_input) == 2
    assert app.button[0].label == "Log in"
    assert not app.exception


def test_signed_out_app_exposes_no_authenticated_navigation(app):
    """No account sidebar, no logout control, nothing page-specific."""
    app.run()

    text = sidebar_text(app)
    assert "Log out" not in text
    assert "Role:" not in text
    assert app.sidebar.button.len == 0


def test_navigation_appears_once_signed_in(app):
    app.session_state["access_token"] = "a-token"
    app.session_state["current_user"] = USER

    with patch("lib.api_client.ApiClient.request") as request:
        request.return_value = ApiResult(
            200, {"status": "ok", "database": "ok", "version": "0.1.0"}
        )
        app.run()

    text = sidebar_text(app)
    assert "Log out" in text
    assert "alice" in text
    assert "Role: USER" in text


def test_navigation_disappears_again_after_logout(app):
    app.session_state["access_token"] = "a-token"
    app.session_state["current_user"] = USER

    with patch("lib.api_client.ApiClient.request") as request:
        request.return_value = ApiResult(
            200, {"status": "ok", "database": "ok", "version": "0.1.0"}
        )
        app.run()
        assert "Log out" in sidebar_text(app)

        app.sidebar.button[0].click().run()
        assert "access_token" not in app.session_state

        # The click run's tree still holds elements rendered before the
        # logout took effect, so assert against the next clean run.
        app.run()

    # Back to the login-only UI: no account sidebar, no logout control.
    assert "Log out" not in sidebar_text(app)
    assert app.sidebar.button.len == 0
    assert len(app.text_input) == 2
    assert not app.exception
