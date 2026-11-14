"""Efficiency dashboard: a home screen and one page per activity.

Run locally:   streamlit run app.py
"""
import streamlit as st

from ui_common import password_ok, NAV_CSS

st.set_page_config(page_title="Efficiency dashboard", page_icon="📦", layout="wide", initial_sidebar_state="collapsed")

if not password_ok():
    st.stop()

pages = [st.Page("home_page.py", title="Home", icon="🏠", default=True),       # opens first: no activity loads until it is chosen
         st.Page("inventory_page.py", title="Inventory", icon="🗄️"),
         st.Page("sample_page.py", title="Sample creation", icon="🧪"),
         st.Page("move_page.py", title="Move", icon="🚚"),
         st.Page("picking_page.py", title="Picking", icon="🧺"),
         st.Page("assembly_page.py", title="Assembly", icon="🧩"),
         st.Page("packing_page.py", title="Packing", icon="📦"),
         st.Page("rto_page.py", title="RTO", icon="↩️"),
         st.Page("dto_page.py", title="DTO", icon="📮"),
         st.Page("checking_page.py", title="Checking", icon="✅")]
try:
    nav = st.navigation(pages, position="top")
except TypeError:                      # older Streamlit: fall back to the sidebar
    nav = st.navigation(pages)
st.markdown(NAV_CSS, unsafe_allow_html=True)
nav.run()
