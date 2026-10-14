"""Efficiency dashboard: Picking, Packing, Checking, Sample creation, Inventory and RTO in one app.

Run locally:   streamlit run app.py
"""
import streamlit as st

from ui_common import password_ok

st.set_page_config(page_title="Efficiency dashboard", page_icon="📦", layout="wide", initial_sidebar_state="collapsed")

if not password_ok():
    st.stop()

pages = [st.Page("picking_page.py", title="Picking", icon="🧺", default=True),
         st.Page("packing_page.py", title="Packing", icon="📦"),
         st.Page("checking_page.py", title="Checking", icon="✅"),
         st.Page("sample_page.py", title="Sample creation", icon="🧪"),
         st.Page("inventory_page.py", title="Inventory", icon="🗄️"),
         st.Page("rto_page.py", title="RTO", icon="↩️")]
try:
    nav = st.navigation(pages, position="top")
except TypeError:                      # older Streamlit: fall back to the sidebar
    nav = st.navigation(pages)
nav.run()
