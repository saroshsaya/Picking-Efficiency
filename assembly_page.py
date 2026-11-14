"""Assembly efficiency page (runs inside app.py's navigation). Same rules as Assembly_Efficiency.xlsx."""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import assembly
from assembly import AssemblySettings
from ui_common import cell_color, style_eff

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("Assembly efficiency vs the standard")
settings_pop = top_s.popover("☰ Settings", use_container_width=True)
if top_r.button("Refresh data", use_container_width=True, key="as_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings
S = AssemblySettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    st.markdown("**Standard time for an order = seconds per drug code x number of different drug codes in the order**")
    S.std_per_code = st.number_input("Standard seconds per drug code", value=float(config.ASM_STD_PER_CODE), step=0.1, min_value=0.1, format="%.1f", key="as_code",
                                     help="For the part the system stamps: the first inventory subtraction of the order to TGR.")
    _max_min = st.number_input("Longest time that can be rated (minutes)", value=float(config.ASM_MAX_SEC) / 60, step=1.0, min_value=1.0, format="%.0f", key="as_max",
                               help="An order whose first subtraction is more than this long before TGR was left open or put on hold: not rated.")
    S.max_sec = _max_min * 60
    S.min_show = st.number_input("Min rated orders to show a percentage", value=1, step=1, min_value=0, key="as_show",
                                 help="A person, the team, or a week / month cell with fewer rated orders shows a dash.")
    S.low_sample = st.number_input("Low-sample marker: rated orders below", value=15, step=1, min_value=0, key="as_low",
                                   help="Week and month cells built on fewer rated orders than this appear in grey italics.")
    st.caption("Time = first inventory subtraction of the order to TGR (order marked assembled). Person = assembler. Names come from config.py.")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
dev_orders, dev_subs = os.environ.get("DEV_ASSEMBLY_ORDERS_CSV"), os.environ.get("DEV_ASSEMBLY_SUBS_CSV")
dev = bool(dev_orders and dev_subs)
live = db.secrets_available() and not dev
if not live and not dev:
    st.info("Assembly efficiency reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()


@st.cache_data(show_spinner="Reading the extract files...")
def _dev_rows(orders, subs):
    return assembly.from_extracts([p.strip() for p in orders.split(",") if p.strip()], subs)


file_rows = _dev_rows(dev_orders, dev_subs) if dev else None

chosen_window = None
with st.container(border=True):
    presets = (["Last 7 days", "Last 30 days", "Last 90 days", "This month", "Last month", "Custom"] if live else ["All data in file", "Custom"])
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date assembled)", presets, index=None, placeholder="Select a period", key="as_preset")
    d_from = d_to = None
    if preset == "Custom":
        if live:
            lo, hi, v_from, v_to = None, None, today - dt.timedelta(days=29), today
        else:
            lo, hi = file_rows["tgr"].min().date(), file_rows["tgr"].max().date(); v_from, v_to = lo, hi
        d_from = p2.date_input("From", value=v_from, min_value=lo, max_value=hi, key="as_from")
        d_to = p3.date_input("To", value=v_to, min_value=lo, max_value=hi, key="as_to")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="as_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All data in file":
        chosen_window = (file_rows["tgr"].min().date(), file_rows["tgr"].max().date())
    elif preset == "Last 7 days":
        chosen_window = (today - dt.timedelta(days=6), today)
    elif preset == "Last 30 days":
        chosen_window = (today - dt.timedelta(days=29), today)
    elif preset == "Last 90 days":
        chosen_window = (today - dt.timedelta(days=89), today)
    elif preset == "This month":
        chosen_window = (today.replace(day=1), today)
    elif preset == "Last month":
        _end = today.replace(day=1) - dt.timedelta(days=1)
        chosen_window = (_end.replace(day=1), _end)

if load_clicked and chosen_window:
    if chosen_window[1] < chosen_window[0]:
        st.error("'To' is before 'From'."); st.stop()
    st.session_state["as_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("as_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end, pad_seconds=0)
if live:
    try:
        raw = db.fetch_assembly(s_ms, e_ms)
    except Exception as e:  # readable message, never the secrets
        st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
        st.stop()
else:
    raw = file_rows[(file_rows["tgr"] >= s_dt) & (file_rows["tgr"] < e_dt)]
if raw.empty:
    st.info(f"No orders assembled between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

orders = assembly.rate_orders(raw, S)
if orders.empty:
    st.info("Every order in this period belongs to a login that is left out in config.py (ASM_EXCLUDED_LOGINS)."); st.stop()
names = assembly.person_order(orders)
st.caption(f"{start:%d %b %Y} to {end:%d %b %Y} (IST, by the date the order was assembled) - {len(orders):,} orders read. Weeks and months at the edges may be partial.")
chosen = st.multiselect("People", names, default=names, key="as_chosen")
view = orders[orders["name"].isin(chosen)] if chosen else orders.iloc[0:0]
rated_view = view[view["rated"]]

# ---------------------------------------------------------------------- header
ov = assembly.overview_table(view, S)
tot = ov.loc["Team"]
n_all, n_rated = len(view), int(view["rated"].sum())
c1, c2, c3, c4 = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="Standard seconds / actual seconds over rated orders. 100% = at the standard; above 100% = faster.")
c2.metric("Standard", f"{S.std_per_code:g} s per drug code", help="Seconds allowed for each different drug code in an order.")
c3.metric("Orders assembled", f"{n_all:,}", help=f"{n_rated:,} could be rated ({(n_rated / n_all if n_all else 0):.0%})")
spc = rated_view["dur"].sum() / rated_view["codes"].sum() if n_rated and rated_view["codes"].sum() > 0 else np.nan
c4.metric("Actual seconds per drug code", "-" if np.isnan(spc) else f"{spc:.1f} s",
          help="Total time of the rated orders / their drug codes. Compare it with the standard.")
st.caption("100% = at the standard for the order's drug codes; above 100% = faster. Time = first inventory subtraction of the order to TGR, on the assembler's login.")

tab_ov, tab_w, tab_m, tab_f, tab_how = st.tabs(["Overview", "Weekly", "Monthly", "Review flags", "How it works"])

# ------------------------------------------------------------------- overview
with tab_ov:
    st.subheader("Each person against the standard")
    show = pd.DataFrame({
        "Orders": ov["orders"].astype(int), "Rated": ov["rated"].astype(int), "Share rated": ov["share_rated"],
        "Drug codes (rated)": ov["codes"].astype(int), "Average seconds per order": ov["avg_sec"], "Efficiency": ov["efficiency"],
    })
    disp = show.copy()
    for c in ("Orders", "Rated", "Drug codes (rated)"):
        disp[c] = show[c].map("{:,}".format)
    disp["Share rated"] = show["Share rated"].map(lambda v: "-" if pd.isna(v) else f"{v:.0%}")
    disp["Average seconds per order"] = show["Average seconds per order"].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
    disp["Efficiency"] = show["Efficiency"].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")
    eff = show["Efficiency"]
    st.dataframe(disp.style.apply(lambda col: [cell_color(v) if pd.notna(v) and 0 < v < 5 else "" for v in eff], subset=["Efficiency"]), width="stretch")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "assembly_overview.csv", "text/csv", key="as_dl_ov")
    st.caption(f"Standard in use: {S.std_per_code:g} seconds per drug code. Orders over {S.max_sec / 60:g} minutes are not rated.")


def period_tab(period, fmt):
    m = assembly.period_matrices(view, S, period)
    if m["efficiency"].empty:
        st.info("No rated orders in this period."); return
    e, p = m["efficiency"].copy(), m["rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated orders; a dash = none (or fewer than {S.min_show})")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T.drop(columns=["Team"], errors="ignore")
    if chart.notna().any().any():
        st.line_chart(chart)
    st.markdown("**Orders rated** (the work behind each percentage)")
    st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch")


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by the date the order was assembled)")
    period_tab("week", "%d-%b")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y")

# --------------------------------------------------------------- review flags
with tab_f:
    st.subheader("Orders that were not rated, and why")
    st.dataframe(assembly.status_counts(view), width="stretch")
    st.markdown("- **No inventory subtraction found**: nothing in the subtraction table for that phone number and order id, so there is no start time.\n"
                "- **First subtraction after TGR**: the start cannot be after the end, so one of the stamps is wrong.\n"
                f"- **Longer than the limit**: more than {S.max_sec / 60:g} minutes from the first subtraction to TGR - left open or put on hold.\n"
                "- **No assembler recorded**: nobody to rate.")
    st.markdown("**Orders over the limit or with a wrong stamp**")
    ft = assembly.flags_table(view)
    st.dataframe(ft.rename(columns={"name": "Person", "order_id": "Order id", "codes": "Drug codes", "first_sub": "First subtraction", "tgr": "Assembled (TGR)",
                                    "dur": "Seconds", "std_sec": "Standard seconds", "review": "What to review"})
                 .style.format({"First subtraction": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M:%S"),
                                "Assembled (TGR)": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M:%S"),
                                "Drug codes": "{:,.0f}", "Seconds": "{:,.0f}", "Standard seconds": "{:.0f}"}, na_rep="-"),
                 width="stretch", height=400, hide_index=True)
    st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "assembly_review_flags.csv", "text/csv", key="as_dl_flags")

# ------------------------------------------------------------------ how it works
with tab_how:
    st.markdown(f"""
### What this answers
How long does each person take to assemble an order, compared with the time it should take? 100% = at the standard, above = faster, below = slower.

### Time
**First inventory subtraction of the order to TGR** (order marked assembled). Orders come from `past_orders` and `cancelled_orders`
(every order with a TGR stamp); the subtractions come from `inventory_subtractions`, linked by phone number and order id.
The earliest subtraction of an order is taken as the start of its assembly. The person is the assembler (`pharmacist`).

### The standard
**{S.std_per_code:g} seconds for each different drug code in the order** (`n_items`). The quantity of each drug is not used.
The system only stamps the part of assembly from the first subtraction to TGR, and the standard is for that part.

### Rated orders
Every order is rated unless something is missing or wrong: no assembler, no subtraction found, no drug code count, the first subtraction after TGR,
or more than **{S.max_sec / 60:g} minutes** from the first subtraction to TGR. Everything not rated is counted on Review flags with the reason.
Not included at all: {", ".join(sorted({assembly.name_of(x).title() for x in config.ASM_EXCLUDED_LOGINS}))}.

### How efficiency is calculated
Add the standard seconds and the actual seconds of a person's rated orders, then divide once (not an average of per-order percentages).

### Caution
Orders still in `active_orders` have no TGR stamp, so they appear only after they move to past or cancelled orders: the last few days can look thin.
""")
    with st.expander("Data checks"):
        chk = assembly.data_checks(orders)
        st.table(pd.DataFrame({"Check": list(chk.keys()), "Value": [f"{v:,}" if isinstance(v, int) else v for v in chk.values()]}))
