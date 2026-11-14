"""Packing efficiency page (runs inside app.py's navigation)."""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import packing as pk
from packing import PackSettings
from ui_common import cell_color, style_eff, settings_area, is_admin, data_checks_box

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("Packing efficiency vs the standard")
settings_pop = settings_area(top_s)
if top_r.button("Refresh data", use_container_width=True, key="pk_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings (like the Excel 'Settings' sheet)
S = PackSettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    S.study_sec_per_item = st.number_input("Recorded average from the time study (sec/item) - reference only", value=config.PACK_STUDY_SEC_PER_ITEM, step=0.01, format="%.2f", key="pk_study",
                                           help="30 cycles, 613 items, 79 minutes. Shown beside each packer's sec/item; NOT the standard used for efficiency.")
    st.markdown("**Standard time for an order = a + b x items + c x distinct SKUs**")
    S.a = st.number_input("a: fixed seconds per order", value=config.PACK_STD_A, step=0.5, format="%.2f", key="pk_a",
                          help="Time every order needs whatever its size. Fitted on real orders.")
    S.b = st.number_input("b: seconds per item", value=config.PACK_STD_B, step=0.05, format="%.2f", key="pk_b", help="Extra seconds for each item.")
    S.c = st.number_input("c: seconds per distinct SKU", value=config.PACK_STD_C, step=0.1, format="%.2f", key="pk_c", help="Extra seconds for each different medicine.")
    S.allowance = st.number_input("Allowance on standard (%)", value=0.0, step=1.0, min_value=0.0, key="pk_allow",
                                  help="Optional extra time allowed over the standard. 0% = the pure standard.") / 100
    S.min_sec = st.number_input("Min packing seconds per order", value=10, step=1, min_value=0, key="pk_min",
                                help="Orders packed faster than this are not rated (probably an early click).")
    S.max_sec = st.number_input("Max packing seconds per order", value=900, step=60, min_value=1, key="pk_max",
                                help="Orders that took longer are not rated and are listed for review (idle time, break or system issue).")
    S.min_show = st.number_input("Min rated orders to show an efficiency cell", value=0, step=5, min_value=0, key="pk_show",
                                 help="0 = show a percentage whenever there is any rated work.")
    S.low_sample = st.number_input("Low-sample marker: rated orders below", value=20, step=5, min_value=0, key="pk_low",
                                   help="Cells built on fewer rated orders than this appear in grey italics.")
    S.max_wait_h = st.number_input("Queue wait counted up to (hours)", value=12, step=1, min_value=1, key="pk_maxwait",
                                   help="Longer waits between assembly and packing are counted separately as next-day waits.")
    S.long_wait_min = st.number_input("Long queue wait: minutes above", value=180, step=15, min_value=0, key="pk_longwait",
                                      help="Orders waiting longer than this (within the limit above) count as long queue waits.")
    S.label_thr_s = st.number_input("Label delay: seconds above", value=60, step=10, min_value=0, key="pk_labthr",
                                    help="A label printed later than this after the Advance click counts as a label delay (normally about 3 seconds).")
    S.label_cap_h = st.number_input("Label delay counted up to (hours)", value=12, step=1, min_value=1, key="pk_labcap",
                                    help="Longer label delays are counted separately as next-day label delays.")
    st.caption("Packer names come from config.py.")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
dev_csv = os.environ.get("DEV_PACK_CSV")
live = db.secrets_available() and not dev_csv
if not live and not dev_csv:
    st.info("Packing reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()
file_orders = db.load_csv(dev_csv) if dev_csv else None
if file_orders is not None:
    for c in ["TGR", "check_start", "TGS", "label_time"]:
        file_orders[c] = pd.to_datetime(file_orders[c], errors="coerce")

chosen_window = None
with st.container(border=True):
    presets = (["Last 7 days", "Last 30 days", "Last 90 days", "This month", "Last month", "Custom"] if live else ["All data in file", "Custom"])
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date packed)", presets, index=None, placeholder="Select a period", key="pk_preset")
    d_from = d_to = None
    if preset == "Custom":
        if live:
            lo, hi, v_from, v_to = None, None, today - dt.timedelta(days=29), today
        else:
            lo, hi = file_orders["TGS"].min().date(), file_orders["TGS"].max().date(); v_from, v_to = lo, hi
        d_from = p2.date_input("From", value=v_from, min_value=lo, max_value=hi, key="pk_from")
        d_to = p3.date_input("To", value=v_to, min_value=lo, max_value=hi, key="pk_to")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="pk_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All data in file":
        chosen_window = (file_orders["TGS"].min().date(), file_orders["TGS"].max().date())
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
    st.session_state["pk_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("pk_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")
if live and (end - start).days > 365:
    st.warning("Large date range - this reads a lot of rows from the database and may take a while.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end, pad_seconds=0)
if live:
    try:
        raw = db.fetch_packing(s_ms, e_ms)
    except Exception as e:  # readable message, never the secrets
        st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
        st.stop()
else:
    raw = file_orders[(file_orders["TGS"] >= s_dt) & (file_orders["TGS"] < e_dt)]
if raw.empty:
    st.info(f"No packed orders between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

orders = pk.rate_orders(raw, S)
orders = orders[(orders["TGS"] >= s_dt) & (orders["TGS"] < e_dt)]
if orders.empty:
    st.info(f"No packed orders between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()
all_packers = pk.packer_order(orders)
st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}.")
chosen = st.multiselect("Packers", all_packers, default=all_packers, key="pk_chosen")
view = orders[orders["name"].isin(chosen)]
if view.empty:
    st.info("Select at least one packer."); st.stop()

# ---------------------------------------------------------------------- header
ov = pk.overview_table(view, S)
tot = ov.loc["All packers"]
c1, c3, _, _ = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="Standard seconds / actual seconds over rated orders. The standard was fitted on past orders, so the team sits near 100% by design.")
c3.metric("Orders packed", f"{int(tot['orders_all']):,}", help=f"{int(tot['orders_rated']):,} rated, {int(tot['orders_not_rated']):,} not rated")
st.caption("100% = at the standard for the order's items and SKUs; above 100% = faster. Packing time = check_start to TGS.")

tab_ov, tab_w, tab_m, tab_d, tab_b, tab_f, tab_how = st.tabs(["Overview", "Weekly", "Monthly", "Daily", "Bottlenecks", "Review flags", "How it works"])

# ------------------------------------------------------------------- overview
with tab_ov:
    st.subheader("Each packer against the standard")
    show = pd.DataFrame({"Orders packed": ov["orders_all"].astype(int), "Actual sec/order": ov["sec_per_order"], "Efficiency": ov["efficiency"]})
    st.dataframe(
        show.style.format({"Actual sec/order": "{:.1f}", "Efficiency": "{:.1%}", "Orders packed": "{:,}"}, na_rep="-")
        .map(lambda v: cell_color(v) if isinstance(v, float) and not np.isnan(v) and 0 < v < 5 else "", subset=["Efficiency"]),
        width="stretch")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "packing_overview.csv", "text/csv", key="pk_dl_ov")


def period_tab(period, fmt, key):
    m = pk.period_matrices(view, S, period)
    e, p = m["efficiency"].copy(), m["orders_rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated orders")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    st.line_chart(chart.drop(columns=[c for c in ["All packers"] if c in chart.columns]))


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by date packed)")
    period_tab("week", "%d-%b", "w")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y", "m")
with tab_d:
    st.subheader("Daily")
    m = pk.period_matrices(view, S, "date")
    e = m["efficiency"].T; p = m["orders_rated"].T
    e.index = [i.strftime("%a %d-%b") if hasattr(i, "strftime") else i for i in e.index]; p.index = e.index
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch", height=600)
    pick_cols = st.multiselect("Show in chart", list(e.columns), default=["All packers"], key="pk_daily_chart")
    if pick_cols:
        st.line_chart(e[pick_cols])

# ----------------------------------------------------------------- bottlenecks
with tab_b:
    st.subheader("Time lost between steps - not part of any packer's efficiency")
    st.caption("Queue wait = assembled (TGR) to packing started (check_start). Label delay = Advance clicked (TGS) to label printed (label_time), normally about 3 seconds. "
               f"Waits are averaged up to {S.max_wait_h:g} h and label delays counted up to {S.label_cap_h:g} h; longer ones are counted separately as next-day.")
    bn = pk.bottlenecks(view, S)
    ren = {"orders": "Orders", "avg_wait": "Avg queue wait (min)", "long_wait": f"Long waits (> {S.long_wait_min:g} min)", "pct_long_wait": "% long waits",
           "next_day_wait": f"Next-day waits (> {S.max_wait_h:g} h)", "wait_hours": "Total queue wait (hours)", "with_label": "Orders with label time",
           "label_delay": f"Label delays (> {S.label_thr_s:g} s)", "pct_label_delay": "% label delays", "label_next_day": "Next-day label delays",
           "label_min": "Total label delay (min)", "avg_label_delay_min": "Avg delay when delayed (min)", "no_label": "Orders with no label time"}
    fm = {"Avg queue wait (min)": "{:.1f}", "% long waits": "{:.1%}", "% label delays": "{:.1%}", "Total queue wait (hours)": "{:,.0f}",
          "Total label delay (min)": "{:,.0f}", "Avg delay when delayed (min)": "{:.1f}"}
    wk = bn["week"].rename(columns=ren); wk.index = [i.strftime("%d-%b") for i in wk.index]
    st.markdown("**By week**"); st.dataframe(wk.style.format(fm, na_rep="-"), width="stretch")
    st.line_chart(wk[["% long waits", "% label delays"]])
    if is_admin():
        ha = bn["hour_assembled"].rename(columns=ren)[["Orders", "Avg queue wait (min)", f"Long waits (> {S.long_wait_min:g} min)", "% long waits", f"Next-day waits (> {S.max_wait_h:g} h)"]]
        ha.index = [f"{int(h):02d}:00" for h in ha.index]
        st.markdown("**Queue wait by the hour the order was assembled (TGR)** - orders assembled late in the day mostly wait until the next day")
        st.dataframe(ha.style.format(fm, na_rep="-"), width="stretch")
        st.bar_chart(ha["Avg queue wait (min)"])
        hd = bn["hour_advanced"].rename(columns=ren)[["Orders", "Orders with label time", f"Label delays (> {S.label_thr_s:g} s)", "% label delays", "Next-day label delays", "Total label delay (min)"]]
        hd.index = [f"{int(h):02d}:00" for h in hd.index]
        st.markdown("**Label delay by the hour the Advance was clicked (TGS)**")
        st.dataframe(hd.style.format(fm, na_rep="-"), width="stretch")
        dd = bn["date"].rename(columns=ren)[["Orders", "Avg queue wait (min)", f"Long waits (> {S.long_wait_min:g} min)", f"Next-day waits (> {S.max_wait_h:g} h)",
                                           f"Label delays (> {S.label_thr_s:g} s)", "Next-day label delays", "Total label delay (min)"]]
        dd.index = [i.strftime("%a %d-%b") for i in dd.index]
        with st.expander("By day"):
            st.dataframe(dd.style.format(fm, na_rep="-"), width="stretch", height=500)

# --------------------------------------------------------------- review flags
with tab_f:
    st.subheader("Orders not rated")
    st.markdown("- **Too long**: longer than the maximum seconds - idle time, a break, or a system issue.\n"
                "- **Too fast**: advanced faster than the minimum seconds - probably an early click.\n"
                "- **End before start**: TGS earlier than check_start - a timestamp problem.\n"
                "- **No start time**: check_start missing, so packing time cannot be measured.\n"
                "- **No item counts**: the order could not be matched to its item and SKU counts.")
    ft = pk.flags_table(view)
    kinds = sorted(ft["status"].unique())
    st.dataframe(ft["status"].value_counts().rename("Orders").to_frame(), width="stretch")
    with st.expander("Show the individual orders"):
        pick = st.multiselect("Show", kinds, default=kinds, key="pk_flag_kinds")
        ft = ft[ft["status"].isin(pick)] if pick else ft.iloc[0:0]
        st.dataframe(ft.rename(columns={"id": "Order ID", "name": "Packer", "date": "Date packed", "check_start": "Packing started", "TGS": "Advance (TGS)",
                                        "dur": "Packing seconds", "total_item_count": "Items", "distinct_sku_count": "Distinct SKUs",
                                        "std_sec": "Standard seconds", "status": "Status"})
                     .style.format({"Date packed": lambda v: v.strftime("%d-%b-%y"), "Packing seconds": "{:,.0f}", "Standard seconds": "{:.0f}", "Items": "{:,.0f}", "Distinct SKUs": "{:,.0f}"}, na_rep="-"),
                     width="stretch", height=450)
        st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "packing_review_flags.csv", "text/csv", key="pk_dl_flags")

# ------------------------------------------------------------------ how it works
with tab_how:
    st.markdown(f"""
### What this answers
How fast does each packer actually pack, compared with the time an order of that size should take? 100% = exactly at the standard, above = faster, below = slower.

### Words used
- **TGR** - the order was assembled and is waiting for a packer. **check_start** - a packer picked it up (start of packing).
  **TGS** - the packer clicked Advance (end of packing). **label_time** - the label was printed, normally seconds after Advance.
- **Packing seconds** = TGS - check_start. **Items / SKUs** come from the order's item counts (customer phone number + order number link the two tables inside the database; no phone numbers reach this page).

### The standard
Standard seconds for an order = **{S.a:g} + {S.b:g} x items + {S.c:g} x distinct SKUs** (x {1 + S.allowance:.2f} allowance). It was fitted on thousands of real orders, so the team overall sits near 100%:
use efficiency to compare packers and to watch changes over time. A flat sec/item rate (the study's {S.study_sec_per_item:g}) is shown for reference only because it ignores order size.

### Rated orders
An order is rated when it has a start time and item counts and took between {S.min_sec:g} and {S.max_sec:g} seconds. Everything else still counts as an order packed and is listed on Review flags.

### How efficiency is calculated
For one packer and one period: add up the standard seconds and the actual seconds of the rated orders, then divide once (not an average of per-order percentages).

### Bottlenecks
Queue wait (assembled to packing started) and label delay (Advance to label) are recorded for later analysis. They never affect efficiency.
""")
    if is_admin():
        data_checks_box(pk.data_checks(orders))
