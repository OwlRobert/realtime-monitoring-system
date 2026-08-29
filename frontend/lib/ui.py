"""Small shared UI helpers."""

from datetime import date, datetime, time
from typing import Any

import streamlit as st

from lib.api_client import ApiResult


def report_error(result: ApiResult) -> None:
    """Turn a failed response into one readable message.

    401 already cleared the session inside `auth.request`, so the next run
    lands on the login form.
    """
    if result.unauthorized:
        st.warning("Your session has expired. Please log in again.")
        st.rerun()
    elif result.status_code == 403:
        st.error("You do not have permission to perform that action.")
    elif result.status_code == 404:
        st.error("That record no longer exists.")
    else:
        st.error(result.error_message)


def start_of_day(value: date | None) -> datetime | None:
    return None if value is None else datetime.combine(value, time.min)


def end_of_day(value: date | None) -> datetime | None:
    return None if value is None else datetime.combine(value, time.max.replace(microsecond=0))


def anomaly_label(is_anomaly: Any) -> str:
    return "⚠️ Yes" if is_anomaly else "No"
