from datetime import date

import pandas as pd
import streamlit as st

from lib import admin_api, auth, ui
from lib.api_client import ApiError

AUDIT_PAGE_KEY = "admin_audit_page"
HISTORY_PAGE_KEY = "admin_history_page"

st.title("🛠️ Administration")

admin = auth.current_user()
if not auth.is_admin():
    # Navigation already hides this page; the backend is the real boundary.
    st.error("Administrator access is required.")
    st.stop()

users_tab, audit_tab, status_tab, history_tab = st.tabs(
    ["Users", "Audit Logs", "Database Status", "Realtime History"]
)


def fetch(call, *args, **kwargs):
    """Run one admin call, reporting failures instead of raising."""
    try:
        result = call(*args, **kwargs)
    except ApiError as exc:
        st.error(str(exc))
        return None
    if not result.ok:
        ui.report_error(result)
        return None
    return result.data


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

with users_tab:
    payload = fetch(admin_api.list_users)
    if payload is not None:
        users = payload["items"]
        table = pd.DataFrame(users)[admin_api.USER_COLUMNS]
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.caption(f"{payload['total']} users.")

        st.subheader("Change a user")
        others = [user for user in users if admin_api.may_change_own(user["id"], admin["id"])]
        if not others:
            st.info("There are no other accounts to manage.")
        else:
            target = st.selectbox(
                "User",
                others,
                format_func=lambda user: f"#{user['id']} {user['username']} ({user['role']})",
                key="admin_target_user",
            )

            role_column, status_column = st.columns(2)
            with role_column:
                new_role = st.selectbox(
                    "Role",
                    admin_api.ROLES,
                    index=admin_api.ROLES.index(target["role"]),
                    key="admin_new_role",
                )
                if st.button("Apply role", use_container_width=True):
                    updated = fetch(admin_api.set_role, target["id"], new_role)
                    if updated:
                        st.success(f"{updated['username']} is now {updated['role']}.")
                        st.rerun()

            with status_column:
                st.write(f"Currently **{'active' if target['is_active'] else 'inactive'}**")
                label = "Deactivate account" if target["is_active"] else "Activate account"
                if st.button(label, use_container_width=True):
                    updated = fetch(
                        admin_api.set_active, target["id"], not target["is_active"]
                    )
                    if updated:
                        state = "active" if updated["is_active"] else "inactive"
                        st.success(f"{updated['username']} is now {state}.")
                        st.rerun()

            st.caption(
                "Your own account is not listed: an administrator cannot demote "
                "or deactivate themselves."
            )

# --------------------------------------------------------------------------
# Audit logs
# --------------------------------------------------------------------------

with audit_tab:
    filter_columns = st.columns(4)
    action = filter_columns[0].selectbox(
        "Action", ["Any", *admin_api.AUDIT_ACTIONS], key="audit_action"
    )
    resource = filter_columns[1].selectbox(
        "Resource", ["Any", *admin_api.RESOURCE_TYPES], key="audit_resource"
    )
    user_filter = filter_columns[2].number_input(
        "User ID", min_value=0, value=0, step=1, key="audit_user"
    )
    use_dates = filter_columns[3].checkbox("Filter by date", key="audit_dates")

    date_columns = st.columns(2)
    from_date = date_columns[0].date_input(
        "From", value=date.today(), key="audit_from", disabled=not use_dates
    )
    to_date = date_columns[1].date_input(
        "To", value=date.today(), key="audit_to", disabled=not use_dates
    )

    signature = (action, resource, user_filter, use_dates, str(from_date), str(to_date))
    if st.session_state.get("admin_audit_signature") != signature:
        st.session_state["admin_audit_signature"] = signature
        st.session_state[AUDIT_PAGE_KEY] = 1

    page = st.session_state.setdefault(AUDIT_PAGE_KEY, 1)
    logs = fetch(
        admin_api.audit_logs,
        page=page,
        action=None if action == "Any" else action,
        resource_type=None if resource == "Any" else resource,
        user_id=int(user_filter) or None,
        start=ui.start_of_day(from_date) if use_dates else None,
        end=ui.end_of_day(to_date) if use_dates else None,
    )

    if logs is not None:
        if not logs["items"]:
            st.info("No audit events match these filters.")
        else:
            table = pd.DataFrame(logs["items"])[admin_api.AUDIT_COLUMNS]
            st.dataframe(table, use_container_width=True, hide_index=True)

        previous, position, following = st.columns([1, 3, 1])
        if previous.button("← Previous", disabled=page <= 1, key="audit_prev",
                           use_container_width=True):
            st.session_state[AUDIT_PAGE_KEY] = page - 1
            st.rerun()
        position.markdown(
            f"**Page {logs['page']} of {max(logs['pages'], 1)}** · {logs['total']} events"
        )
        if following.button("Next →", disabled=page >= logs["pages"], key="audit_next",
                            use_container_width=True):
            st.session_state[AUDIT_PAGE_KEY] = page + 1
            st.rerun()

# --------------------------------------------------------------------------
# Database status
# --------------------------------------------------------------------------

with status_tab:
    status = fetch(admin_api.database_status)
    if status is not None:
        label = admin_api.health_label(status)
        (st.success if status["healthy"] else st.error)(f"Database: {label}")

        counts = st.columns(4)
        counts[0].metric("Users", f"{status['users']:,}")
        counts[1].metric("Data records", f"{status['data_records']:,}")
        counts[2].metric("Realtime records", f"{status['realtime_records']:,}")
        counts[3].metric("Audit logs", f"{status['audit_logs']:,}")

        st.caption(
            f"Latest realtime reading: {status['latest_realtime_timestamp'] or '—'}"
        )

        st.subheader("Connection")
        connection = st.columns(3)
        connection[0].metric("Dialect", status["dialect"])
        connection[1].metric("Driver", status["driver"])
        connection[2].metric("Database", status["database"])

        st.subheader("Connection pool")
        pool = status["pool"]
        pool_columns = st.columns(4)
        pool_columns[0].metric("Size", pool["size"] if pool["size"] is not None else "—")
        pool_columns[1].metric("Checked in", pool["checked_in"] if pool["checked_in"] is not None else "—")
        pool_columns[2].metric("Checked out", pool["checked_out"] if pool["checked_out"] is not None else "—")
        pool_columns[3].metric("Overflow", pool["overflow"] if pool["overflow"] is not None else "—")
        st.caption("Host, user and password are never exposed by this endpoint.")

# --------------------------------------------------------------------------
# Realtime history (persisted rows, not the live stream)
# --------------------------------------------------------------------------

with history_tab:
    st.caption(
        "Persisted realtime readings from MariaDB, newest first. "
        "The live stream is on the Realtime page."
    )
    category = st.text_input("Category", key="history_category").strip()

    if st.session_state.get("admin_history_category") != category:
        st.session_state["admin_history_category"] = category
        st.session_state[HISTORY_PAGE_KEY] = 1

    page = st.session_state.setdefault(HISTORY_PAGE_KEY, 1)
    history = fetch(admin_api.realtime_history, page=page, category=category or None)

    if history is not None:
        if not history["items"]:
            st.info("No persisted realtime records yet.")
        else:
            frame = pd.DataFrame(history["items"])[
                ["timestamp", "title", "value", "category", "is_anomaly"]
            ]
            frame["is_anomaly"] = frame["is_anomaly"].map(ui.anomaly_label)
            frame = frame.rename(columns={"is_anomaly": "anomaly"})
            st.dataframe(frame, use_container_width=True, hide_index=True)

            anomalies = sum(1 for item in history["items"] if item["is_anomaly"])
            if anomalies:
                st.warning(f"⚠️ {anomalies} anomalies on this page.")

        previous, position, following = st.columns([1, 3, 1])
        if previous.button("← Previous", disabled=page <= 1, key="history_prev",
                           use_container_width=True):
            st.session_state[HISTORY_PAGE_KEY] = page - 1
            st.rerun()
        position.markdown(
            f"**Page {history['page']} of {max(history['pages'], 1)}**"
            f" · {history['total']:,} realtime records"
        )
        if following.button("Next →", disabled=page >= history["pages"], key="history_next",
                            use_container_width=True):
            st.session_state[HISTORY_PAGE_KEY] = page + 1
            st.rerun()
