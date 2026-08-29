from datetime import date

import pandas as pd
import streamlit as st

from lib import analytics_api, records_api, ui
from lib.api_client import ApiError

st.title("📊 Analytics")
st.caption("All figures are computed by the backend analytics API.")

# --------------------------------------------------------------------------
# One filter set, applied to summary, categories and trend
# --------------------------------------------------------------------------

with st.expander("Filters", expanded=True):
    row_one = st.columns(4)
    category = row_one[0].text_input("Category", key="a_category").strip()
    source = row_one[1].selectbox("Source", ["Any", *records_api.SOURCES], key="a_source")
    interval = row_one[2].selectbox("Trend interval", analytics_api.INTERVALS, key="a_interval")
    use_dates = row_one[3].checkbox("Filter by date", key="a_use_dates")

    row_two = st.columns(2)
    start_date = row_two[0].date_input(
        "From", value=date.today(), key="a_start", disabled=not use_dates
    )
    end_date = row_two[1].date_input(
        "To", value=date.today(), key="a_end", disabled=not use_dates
    )

    if st.button("Reset filters"):
        for key in ("a_category", "a_source", "a_interval", "a_use_dates"):
            st.session_state.pop(key, None)
        st.rerun()

filters = {
    "category": category or None,
    "source": None if source == "Any" else source,
    "start": ui.start_of_day(start_date) if use_dates else None,
    "end": ui.end_of_day(end_date) if use_dates else None,
}


def fetch(call, **extra):
    """Run one analytics call, reporting failures instead of raising."""
    try:
        result = call(**filters, **extra)
    except ApiError as exc:
        st.error(str(exc))
        return None
    if not result.ok:
        ui.report_error(result)
        return None
    return result.data


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

st.subheader("Summary")
summary = fetch(analytics_api.summary)

if summary is not None:
    if not analytics_api.has_data(summary):
        st.info("No records match these filters.")
    columns = st.columns(5)
    columns[0].metric("Count", f"{summary['count']:,}")
    columns[1].metric("Total", analytics_api.format_metric(summary["total"]))
    columns[2].metric("Average", analytics_api.format_metric(summary["average"]))
    columns[3].metric("Minimum", analytics_api.format_metric(summary["minimum"]))
    columns[4].metric("Maximum", analytics_api.format_metric(summary["maximum"]))

st.divider()

# --------------------------------------------------------------------------
# Category aggregation
# --------------------------------------------------------------------------

st.subheader("By category")
category_payload = fetch(analytics_api.categories)

if category_payload is not None:
    items = category_payload["items"]
    if not items:
        st.info("No categories match these filters.")
    else:
        table = pd.DataFrame(items)
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.caption("Average value per category")
        st.bar_chart(table.set_index("category")["average"])

st.divider()

# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------

st.subheader("Trend")
trend_payload = fetch(analytics_api.trend, interval=interval)

if trend_payload is not None:
    points = trend_payload["points"]
    if not points:
        st.info("No data in this range.")
    else:
        series = pd.DataFrame(points)
        series["bucket"] = pd.to_datetime(series["bucket"])
        st.caption(
            f"Average value per {trend_payload['interval']}"
            f" · {len(points)} buckets · empty buckets are omitted"
        )
        st.line_chart(series.set_index("bucket")["average"])

        with st.expander("Trend data"):
            st.dataframe(series, use_container_width=True, hide_index=True)
