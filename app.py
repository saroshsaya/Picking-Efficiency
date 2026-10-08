"""Efficiency dashboard: Picking and Packing in one app.

Run locally:   streamlit run app.py
"""
import streamlit as st

from ui_common import password_ok

st.set_page_config(page_title="Efficiency dashboard", page_icon="📦", layout="wide", initial_sidebar_state="collapsed")

if not password_ok():
    st.stop()

pages = [st.Page("picking_page.py", title="Picking", icon="🧺", default=True),
         st.Page("packing_page.py", title="Packing", icon="📦")]
try:
    nav = st.navigation(pages, position="top")
except TypeError:                      # older Streamlit: fall back to the sidebar
    nav = st.navigation(pages)
nav.run()
