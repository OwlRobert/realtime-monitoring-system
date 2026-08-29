from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from lib import auth, records_api, ui
from lib.api_client import ApiError

PAGE_KEY = "records_page"
FILTER_KEY = "records_filter_signature"
CONFIRM_KEY = "records_pending_delete"

st.title("🗂️ Records")

user = auth.current_user()
may_write = auth.can_write()

# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

with st.expander("Filters", expanded=True):
    row_one = st.columns(4)
    category = row_one[0].text_input("Category", key="f_category").strip()
    source = row_one[1].selectbox(
        "Source", ["Any", *records_api.SOURCES], key="f_source"
    )
    sort_by = row_one[2].selectbox("Sort by", records_api.SORT_FIELDS, key="f_sort_by")
    order = row_one[3].selectbox("Order", records_api.SORT_ORDERS, key="f_order")

    row_two = st.columns(4)
    use_dates = row_two[0].checkbox("Filter by date", key="f_use_dates")
    start_date = row_two[1].date_input(
        "From", value=date.today(), key="f_start", disabled=not use_dates
    )
    end_date = row_two[2].date_input(
        "To", value=date.today(), key="f_end", disabled=not use_dates
    )
    page_size = row_two[3].selectbox(
        "Page size", records_api.PAGE_SIZE_OPTIONS, index=1, key="f_page_size"
    )

    if st.button("Reset filters"):
        for key in ("f_category", "f_source", "f_sort_by", "f_order", "f_use_dates",
                    "f_page_size", FILTER_KEY):
            st.session_state.pop(key, None)
        st.session_state[PAGE_KEY] = 1
        st.rerun()

start = ui.start_of_day(start_date) if use_dates else None
end = ui.end_of_day(end_date) if use_dates else None
source_filter = None if source == "Any" else source

# Any change of filters returns to the first page.
signature = (category, source_filter, sort_by, order, str(start), str(end), page_size)
if st.session_state.get(FILTER_KEY) != signature:
    st.session_state[FILTER_KEY] = signature
    st.session_state[PAGE_KEY] = 1

page = st.session_state.setdefault(PAGE_KEY, 1)

# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------

try:
    result = records_api.list_records(
        page=page,
        page_size=page_size,
        category=category or None,
        source=source_filter,
        start=start,
        end=end,
        sort_by=sort_by,
        order=order,
    )
except ApiError as exc:
    st.error(str(exc))
    st.stop()

if not result.ok:
    ui.report_error(result)
    st.stop()

payload = result.data
records = payload["items"]
pages = payload["pages"]

# A deletion may have emptied the last page.
if page > max(pages, 1):
    st.session_state[PAGE_KEY] = records_api.clamp_page(page, pages)
    st.rerun()

# --------------------------------------------------------------------------
# Table
# --------------------------------------------------------------------------

if not records:
    st.info("No records match these filters.")
else:
    table = pd.DataFrame(records)[records_api.TABLE_COLUMNS]
    table["is_anomaly"] = table["is_anomaly"].map(ui.anomaly_label)
    table = table.rename(columns={"is_anomaly": "anomaly", "owner_id": "owner"})
    st.dataframe(table, use_container_width=True, hide_index=True)

    anomalies = sum(1 for record in records if record["is_anomaly"])
    if anomalies:
        st.warning(f"⚠️ {anomalies} of {len(records)} records on this page are anomalies.")

# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

previous, status, following = st.columns([1, 3, 1])
if previous.button("← Previous", disabled=page <= 1, use_container_width=True):
    st.session_state[PAGE_KEY] = page - 1
    st.rerun()
status.markdown(
    f"**Page {page} of {max(pages, 1)}** · {payload['total']} records in total"
)
if following.button("Next →", disabled=page >= pages, use_container_width=True):
    st.session_state[PAGE_KEY] = page + 1
    st.rerun()

# --------------------------------------------------------------------------
# Write controls
# --------------------------------------------------------------------------

if not may_write:
    st.divider()
    st.caption("Your role has read-only access to records.")
    st.stop()

st.divider()
create_tab, edit_tab, delete_tab = st.tabs(["Create", "Edit", "Delete"])

with create_tab:
    with st.form("create_record", clear_on_submit=True):
        columns = st.columns(2)
        title = columns[0].text_input("Title")
        new_category = columns[1].text_input("Category")
        value = columns[0].number_input("Value", value=0.0, format="%.2f")
        new_source = columns[1].selectbox("Source", records_api.CREATABLE_SOURCES)
        on_date = columns[0].date_input("Date", value=date.today())
        on_time = columns[1].time_input("Time", value=datetime.now().time())
        submitted = st.form_submit_button("Create record")

    if submitted:
        payload = {
            "title": title,
            "value": float(value),
            "category": new_category,
            "timestamp": datetime.combine(on_date, on_time).replace(microsecond=0).isoformat(),
            "source": new_source,
        }
        try:
            created = records_api.create_record(payload)
        except ApiError as exc:
            st.error(str(exc))
        else:
            if created.ok:
                st.success(f"Created record #{created.data['id']}.")
                st.rerun()
            else:
                ui.report_error(created)

editable = records_api.modifiable(records, user)

with edit_tab:
    if not editable:
        st.info("No records on this page belong to you.")
    else:
        chosen = st.selectbox(
            "Record",
            editable,
            format_func=records_api.describe,
            key="edit_choice",
        )
        with st.form("edit_record"):
            columns = st.columns(2)
            new_title = columns[0].text_input("Title", value=chosen["title"])
            new_cat = columns[1].text_input("Category", value=chosen["category"])
            new_value = columns[0].number_input(
                "Value", value=float(chosen["value"]), format="%.2f"
            )
            moment = datetime.fromisoformat(chosen["timestamp"])
            new_date = columns[1].date_input("Date", value=moment.date())
            new_time = columns[0].time_input("Time", value=moment.time())
            st.caption("Source, owner, anomaly flag and timestamps are server-managed.")
            saved = st.form_submit_button("Save changes")

        if saved:
            submitted_values = {
                "title": new_title,
                "category": new_cat,
                "value": float(new_value),
                "timestamp": datetime.combine(new_date, new_time)
                .replace(microsecond=0)
                .isoformat(),
            }
            changes = records_api.changed_fields(chosen, submitted_values)
            if not changes:
                st.info("Nothing changed.")
            else:
                try:
                    updated = records_api.update_record(chosen["id"], changes)
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    if updated.ok:
                        st.success(f"Updated record #{chosen['id']}.")
                        st.rerun()
                    else:
                        ui.report_error(updated)

with delete_tab:
    if not editable:
        st.info("No records on this page belong to you.")
    else:
        target = st.selectbox(
            "Record",
            editable,
            format_func=records_api.describe,
            key="delete_choice",
        )
        pending = st.session_state.get(CONFIRM_KEY)

        if pending != target["id"]:
            if st.button("Delete record", type="primary"):
                st.session_state[CONFIRM_KEY] = target["id"]
                st.rerun()
        else:
            st.warning(f"Permanently delete record #{target['id']}?")
            confirm, cancel = st.columns(2)
            if confirm.button("Yes, delete", type="primary"):
                st.session_state.pop(CONFIRM_KEY, None)
                try:
                    removed = records_api.delete_record(target["id"])
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    if removed.ok:
                        st.success(f"Deleted record #{target['id']}.")
                        st.rerun()
                    else:
                        ui.report_error(removed)
            if cancel.button("Cancel"):
                st.session_state.pop(CONFIRM_KEY, None)
                st.rerun()
