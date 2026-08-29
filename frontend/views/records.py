import streamlit as st

from lib import auth

st.title("🗂️ Records")
st.info("Coming next: browse, filter, create, edit and delete data records.")

if auth.can_write():
    st.caption("Your role may create and edit records.")
else:
    st.caption("Your role has read-only access to records.")
