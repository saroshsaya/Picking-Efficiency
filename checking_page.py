"""Checking efficiency page (runs inside app.py's navigation)."""
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import checking as ck
from checking import CheckSettings
from ui_common import cell_color, style_eff

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("Checking efficiency vs the standard")
settings_pop = top_s.popover("☰ Settings", use_container_width=True)
if top_r.button("Refresh data", use_container_width=True, key="ck_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings (like the Excel 'Settings' sheet)
S = CheckSettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    S.id_std = st.number_input("ID checking standard (seconds per task)", value=config.CHECK_ID_STD_SEC, step=0.5, format="%.2f", key="ck_idstd",
                               help="Mean time of a rated ID check (ID_CUBICLE and ID_RACK). Every ID check is held to this.")
    st.markdown("**Quantity checking standard = a + b x LN(1 + quantity)**")
    S.a = st.number_input("a: fixed seconds per task", value=config.CHECK_QTY_A, step=0.5, format="%.2f", key="ck_a",
                          help="Time every quantity check (ST, LT) needs whatever its quantity.")
    S.b = st.number_input("b: seconds per LN(1 + quantity)", value=config.CHECK_QTY_B, step=0.1, format="%.2f", key="ck_b",
                          help="How much longer bigger quantities take. Each doubling of the quantity adds about 0.69 x b seconds.")
    S.allowance = st.number_input("Allowance on standard (%)", value=0.0, step=1.0, min_value=0.0, key="ck_allow",
                                  help="Optional extra time allowed over the standard. 0% = the pure standard.") / 100
    S.min_sec = st.number_input("Min seconds per task", value=1, step=1, min_value=0, key="ck_min", help="Tasks finished faster than this are not rated.")
    S.max_sec = st.number_input("Max seconds per task (300 = 5 minutes)", value=config.CHECK_MAX_SEC, step=30, min_value=1, key="ck_max",
                                help="Tasks that took longer are not rated and are flagged for review.")
    S.min_show = st.number_input("Min rated tasks to show an efficiency cell", value=0, step=5, min_value=0, key="ck_show",
                                 help="0 = show a percentage whenever there is any rated work.")
    S.low_sample = st.number_input("Low-sample marker: rated tasks below", value=20, step=5, min_value=0, key="ck_low",
                                   help="Cells built on fewer rated tasks than this appear in grey italics.")
    st.caption("Checker names come from config.py.")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
if not db.secrets_available():
    st.info("Checking reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()

chosen_window = None
with st.container(border=True):
    presets = ["Last 7 days", "Last 30 days", "Last 90 days", "This month", "Last month", "Custom"]
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date started)", presets, index=None, placeholder="Select a period", key="ck_preset")
    d_from = d_to = None
    if preset == "Custom":
        d_from = p2.date_input("From", value=today - dt.timedelta(days=29), key="ck_from")
        d_to = p3.date_input("To", value=today, key="ck_to")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="ck_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
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
    st.session_state["ck_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("ck_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")
if (end - start).days > 365:
    st.warning("Large date range - this reads a lot of rows from the database and may take a while.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end, pad_seconds=0)
try:
    raw = db.fetch_checking(s_ms, e_ms)
except Exception as e:  # readable message, never the secrets
    st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
    st.stop()
if raw.empty:
    st.info(f"No checking tasks between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

tasks = ck.rate_tasks(raw, S)
all_checkers = ck.checker_order(tasks)
st.caption(f"{start:%d %b %Y} to {end:%d %b %Y} (IST, by date started) - {len(raw):,} tasks read. Weeks and months at the edges of the period may be partial.")
chosen = st.multiselect("Checkers", all_checkers, default=all_checkers, key="ck_chosen")
view = tasks[tasks["name"].isin(chosen)]
if view.empty:
    st.info("Select at least one checker."); st.stop()

# ---------------------------------------------------------------------- header
ov = ck.overview_table(view, S)
tot = ov.loc["All checkers"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="Standard seconds / actual seconds over rated tasks. The standards were set from past checks, so the team sits near 100% by design.")
c2.metric("ID check standard", f"{S.id_std * (1 + S.allowance):.1f} s")
c3.metric("Tasks", f"{int(tot['tasks_all']):,}", help=f"{int(tot['tasks_rated']):,} rated, {int(tot['tasks_not_rated']):,} not rated")
c4.metric(f"Tasks over {S.max_sec / 60:g} min (flagged)", f"{int(tot['over_limit']):,}")
st.caption("100% = at the standard for the kind of task; above 100% = faster. Checking time = started to finished.")

tab_ov, tab_w, tab_m, tab_d, tab_f, tab_how = st.tabs(["Standard vs actual", "Weekly", "Monthly", "Daily", "Review flags", "How it works"])

# ------------------------------------------------------------------- overview
with tab_ov:
    st.subheader("Each checker's actual pace against the standard")
    st.markdown("**Standard seconds**")
    st.dataframe(ck.standard_samples(S).style.format({"Standard seconds": "{:.0f}", "Quantity": "{:,.0f}"}, na_rep="-"), width="stretch", hide_index=True)
    show = pd.DataFrame({
        "Tasks (all)": ov["tasks_all"].astype(int), "Tasks rated": ov["tasks_rated"].astype(int), "Tasks not rated": ov["tasks_not_rated"].astype(int),
        "Over the time limit (flagged)": ov["over_limit"].astype(int), "Actual sec/task": ov["sec_per_task"], "Standard sec/task": ov["std_per_task"],
        "Efficiency (overall)": ov["efficiency"], "Above / below average": ov["verdict"],
        "ID checks rated": ov["id_rated"], "ID efficiency": ov["id_eff"], "Quantity checks rated": ov["qty_rated"], "Quantity efficiency": ov["qty_eff"],
    })
    st.dataframe(
        show.style.format({"Actual sec/task": "{:.1f}", "Standard sec/task": "{:.1f}", "Efficiency (overall)": "{:.1%}", "ID efficiency": "{:.1%}", "Quantity efficiency": "{:.1%}",
                           "Tasks (all)": "{:,}", "Tasks rated": "{:,}", "Tasks not rated": "{:,}"}, na_rep="-")
        .map(lambda v: cell_color(v) if isinstance(v, float) and not np.isnan(v) and 0 < v < 5 else "", subset=["Efficiency (overall)", "ID efficiency", "Quantity efficiency"]),
        width="stretch")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "checking_standard_vs_actual.csv", "text/csv", key="ck_dl_ov")
    st.caption("Efficiency (overall) adds the standard seconds and the actual seconds across both activities. The ID and quantity columns show each activity on its own. "
               "Tasks over the limit are flagged and not rated; tasks with a missing timestamp are not considered.")


def period_tab(period, fmt):
    m = ck.period_matrices(view, S, period)
    e, p = m["efficiency"].copy(), m["tasks_rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated tasks")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    st.line_chart(chart.drop(columns=[c for c in ["All checkers"] if c in chart.columns]))
    st.markdown("**Tasks rated** (the work behind each percentage)")
    st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch")
    st.markdown("**Actual sec/task**")
    sec = m["sec_per_task"].copy(); sec.columns = e.columns
    st.dataframe(sec.style.format("{:.1f}", na_rep="-"), width="stretch")
    st.markdown("**Tasks, all (including tasks not rated)**")
    al = m["tasks_all"].copy(); al.columns = e.columns
    st.dataframe(al.style.format("{:,.0f}", na_rep="-"), width="stretch")


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by date started)")
    period_tab("week", "%d-%b")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y")
with tab_d:
    st.subheader("Daily")
    m = ck.period_matrices(view, S, "date")
    e = m["efficiency"].T; p = m["tasks_rated"].T
    e.index = [i.strftime("%a %d-%b") if hasattr(i, "strftime") else i for i in e.index]; p.index = e.index
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch", height=600)
    pick_cols = st.multiselect("Show in chart", list(e.columns), default=["All checkers"], key="ck_daily_chart")
    if pick_cols:
        st.line_chart(e[pick_cols])
    with st.expander("Tasks rated per day"):
        st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch", height=500)

# --------------------------------------------------------------- review flags
with tab_f:
    st.subheader("Tasks not rated")
    st.markdown("- **Over the time limit (flagged)**: longer than the maximum seconds - idle time, a break, a task left open, or a system issue.\n"
                "- **Missing timestamp**: no finish time, so the duration cannot be measured.\n"
                "- **No quantity**: a quantity check without a quantity, so no standard can be set.\n"
                "- **No time elapsed**: finished in less than the minimum seconds.")
    ft = ck.flags_table(view)
    kinds = sorted(ft["status"].unique())
    pick = st.multiselect("Show", kinds, default=kinds, key="ck_flag_kinds")
    ft = ft[ft["status"].isin(pick)] if pick else ft.iloc[0:0]
    st.dataframe(ft.rename(columns={"task": "Task #", "name": "Checker", "type": "Type", "activity": "Activity", "qty_checked": "Qty checked",
                                    "created_at": "Started", "time": "Finished", "dur": "Seconds", "std_sec": "Standard seconds", "status": "Status"})
                 .style.format({"Qty checked": "{:,.0f}", "Seconds": "{:,.0f}", "Standard seconds": "{:.0f}"}, na_rep="-"), width="stretch", height=450)
    st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "checking_review_flags.csv", "text/csv", key="ck_dl_flags")

# ------------------------------------------------------------------ how it works
with tab_how:
    st.markdown(f"""
### What this answers
How fast does each checker actually check, compared with the time a task of that kind should take? 100% = exactly at the standard, above = faster, below = slower.
There was no time study for checking, so the standards come from past checks.

### Two kinds of checking
- **ID checking** (ID_CUBICLE, ID_RACK): short tasks with no quantity. Standard: **{S.id_std:g} seconds** each - the mean of rated ID checks.
- **Quantity checking** (ST, LT): counting stock. Standard: **{S.a:g} + {S.b:g} x LN(1 + quantity)** seconds, so bigger quantities get more time, flattening out
  (each doubling adds about {S.b * np.log(2):.0f} seconds).

The page shows one overall efficiency per checker; behind it the two activities are calculated separately (their own columns on the first tab).
One standard for everything would reward the task mix, not the speed.

### Rated tasks
A task is rated when it has a start and a finish time, took between {S.min_sec:g} and {S.max_sec:g} seconds ({S.max_sec / 60:g} minutes), and - for quantity checks - has a quantity.
Tasks over the limit are flagged on Review flags; tasks with a missing timestamp are not considered. Both still count as tasks (the 'all' columns).

### How efficiency is calculated
For one checker and one period: add up the standard seconds and the actual seconds of the rated tasks, then divide once (not an average of per-task percentages).

### Limits
Quantity explains only a little of the variation in checking time. The standards were set from past checks, so the team sits near 100%: read efficiency as a comparison
between checkers and over time. Checkers with few tasks should be read with care (grey markers).
""")
    with st.expander("Data checks"):
        chk = ck.data_checks(tasks)
        st.table(pd.DataFrame({"Check": list(chk.keys()), "Value": [f"{v:,}" if isinstance(v, int) else v for v in chk.values()]}))
