"""Home screen: nothing is read from the database here. Pick an activity to open it."""
import streamlit as st

ACTIVITIES = [
    ("inventory_page.py", "Inventory", "🗄️", "Slot verification"),
    ("sample_page.py", "Sample creation", "🧪", "Testing samples"),
    ("move_page.py", "Move", "🚚", "LT rack to ST cubicle"),
    ("picking_page.py", "Picking", "🧺", "Make lists"),
    ("assembly_page.py", "Assembly", "🧩", "Orders assembled"),
    ("packing_page.py", "Packing", "📦", "Orders packed"),
    ("rto_page.py", "RTO", "↩️", "Returned parcels"),
    ("dto_page.py", "DTO", "📮", "Customer returns by courier"),
    ("checking_page.py", "Checking", "✅", "ID and quantity checks"),
]

st.title("Efficiency dashboard")
st.caption("Choose an activity. Each one compares every person's actual time with the standard: 100% = at the standard, above 100% = faster.")

for row in range(0, len(ACTIVITIES), 3):
    for col, (page, title, icon, what) in zip(st.columns(3), ACTIVITIES[row:row + 3]):
        with col.container(border=True):
            st.page_link(page, label=f"**{title}**", icon=icon, use_container_width=True)
            st.caption(what)

st.divider()
on = st.toggle("Admin tools", value=bool(st.session_state.get("admin_on", False)),
               help="Shows the Settings button, the data checks and the diagnostic tables on every activity. Leave off for everyday use.")
st.session_state["admin_on"] = on
