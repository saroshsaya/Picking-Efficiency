"""Inventory (slot verification) efficiency page (runs inside app.py's navigation)."""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import inventory as inv
from inventory import InvSettings
from ui_common import cell_color, style_eff

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("Inventory efficiency vs the standard")
settings_pop = top_s.popover("☰ Settings", use_container_width=True)
if top_r.button("Refresh data", use_container_width=True, key="iv_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings
S = InvSettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    S.std_sec = st.number_input("Standard seconds per verification", value=config.INV_STD_SEC, step=10.0, format="%.1f", key="iv_std",
                                help="One verification = first value ticked in VERIFY to quantity found entered, for one drug code. Default = mean of same-day verifications up to the cap, so the team sits near 100%.")
    S.max_sec = st.number_input("Max seconds per verification (cap)", value=config.INV_MAX_SEC, step=60, min_value=1, key="iv_max",
                                help="Longer ones are not rated and are listed on Review flags (break, interruption or a stuck screen). 1800 = 30 min.")
    S.exclude_overnight = st.checkbox("Do not rate overnight verifications", value=True, key="iv_night")
    S.min_show = st.number_input("Min rated jobs to show an efficiency cell", value=10, step=1, min_value=0, key="iv_show",
                                 help="Fewer rated jobs than this show blank: too few to judge a person.")
    S.low_sample = st.number_input("Low-sample marker: rated jobs below", value=10, step=1, min_value=0, key="iv_low",
                                   help="Cells built on fewer rated jobs than this appear in grey italics.")
    st.caption("Person = checked_by (the same person verifies and checks). Names come from config.py.")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
dev_csv = os.environ.get("DEV_INV_CSV")
live = db.secrets_available() and not dev_csv
if not live and not dev_csv:
    st.info("Inventory reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()
file_rows = None
if dev_csv:
    file_rows = pd.read_csv(dev_csv)
    for c in ["verify_start", "verify_finish", "checked_time"]:
        file_rows[c] = pd.to_datetime(file_rows[c], errors="coerce")
    file_rows = file_rows[file_rows["verify_start"].notna()]

chosen_window = None
with st.container(border=True):
    presets = (["Last 7 days", "Last 30 days", "Last 90 days", "This month", "Last month", "Custom"] if live else ["All data in file", "Custom"])
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date verified)", presets, index=None, placeholder="Select a period", key="iv_preset")
    d_from = d_to = None
    if preset == "Custom":
        if live:
            lo, hi, v_from, v_to = None, None, today - dt.timedelta(days=29), today
        else:
            lo, hi = file_rows["verify_start"].min().date(), file_rows["verify_start"].max().date(); v_from, v_to = lo, hi
        d_from = p2.date_input("From", value=v_from, min_value=lo, max_value=hi, key="iv_from")
        d_to = p3.date_input("To", value=v_to, min_value=lo, max_value=hi, key="iv_to")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="iv_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All data in file":
        chosen_window = (file_rows["verify_start"].min().date(), file_rows["verify_start"].max().date())
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
    st.session_state["iv_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("iv_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data. Verification stamps only exist since 18 Aug 2026.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end, pad_seconds=0)
if live:
    try:
        raw = db.fetch_inventory(s_ms, e_ms)
    except Exception as e:  # readable message, never the secrets
        st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
        st.stop()
else:
    raw = file_rows[(file_rows["verify_start"] >= s_dt) & (file_rows["verify_start"] < e_dt)]
if raw.empty:
    st.info(f"No verifications between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

rows = inv.rate_verifications(raw, S)
names = inv.checker_order(rows)
st.caption(f"{start:%d %b %Y} to {end:%d %b %Y} (IST, by date verified) - {len(raw):,} verifications read. Weeks and months at the edges may be partial.")
if not names:
    st.warning("No verification in this period could be rated. See Review flags for the reasons.")
chosen = st.multiselect("Checkers", names, default=names, key="iv_chosen")
view = rows[rows["name"].isin(chosen) | ~rows["rated"]] if chosen else rows[~rows["rated"]]
rated_view = view[view["rated"]]

# ---------------------------------------------------------------------- header
ov = inv.overview_table(view, S)
tot = ov.loc["All checkers"] if "All checkers" in ov.index else None
c1, c2, c3, c4 = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if tot is None or pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="Standard seconds / actual seconds over rated verifications. The standard is the team mean, so the team sits near 100% by design.")
c2.metric("Standard per verification", f"{S.std_sec / 60:.1f} min")
c3.metric("Verifications", f"{len(rows):,}", help=f"{int(rows['rated'].sum()):,} rated")
med = rated_view["dur"].median() / 60 if len(rated_view) else np.nan
c4.metric("Median time of rated jobs", "-" if np.isnan(med) else f"{med:.1f} min")
st.caption("100% = at the standard; above 100% = faster. Time = verify_start to checked_time, on the person who checked.")

tab_ov, tab_w, tab_m, tab_f, tab_how = st.tabs(["Overview", "Weekly", "Monthly", "Review flags", "How it works"])

with tab_ov:
    st.subheader("Each checker against the standard")
    show = pd.DataFrame({
        "Jobs rated": ov["jobs_rated"].astype(int), "Actual sec/job": ov["sec_per_job"], "Median sec": ov["median_sec"],
        "Avg quantity found": ov["avg_qty"], "Efficiency": ov["efficiency"], "Above / below team": ov["verdict"], "Not rated": ov["not_rated"]})
    st.dataframe(
        show.style.format({"Actual sec/job": "{:.0f}", "Median sec": "{:.0f}", "Avg quantity found": "{:.0f}", "Efficiency": "{:.1%}", "Jobs rated": "{:,}"}, na_rep="-")
        .map(lambda v: cell_color(v) if isinstance(v, float) and not np.isnan(v) and 0 < v < 5 else "", subset=["Efficiency"]),
        width="stretch")
    st.caption(f"Efficiency is blank for people with fewer than {S.min_show} rated jobs. Not rated = overnight, over the cap, or a timestamp problem.")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "inventory_overview.csv", "text/csv", key="iv_dl_ov")
    sug = inv.suggested_standard(rows); r2 = inv.quantity_r2(rows)
    bits = []
    if sug:
        bits.append(f"Mean of rated jobs in this period: {sug:.0f} s (current standard {S.std_sec:.0f} s).")
    if r2 is not None:
        bits.append(f"Quantity found explains {r2:.0%} of the variation in time (R² {r2:.2f}), so no quantity term is used.")
    if bits:
        st.caption(" ".join(bits))


def period_tab(period, fmt):
    m = inv.period_matrices(view, S, period)
    if m["efficiency"].empty:
        st.info("No rated verifications in this period."); return
    e, p = m["efficiency"].copy(), m["jobs_rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated jobs")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    st.line_chart(chart.drop(columns=[c for c in ["All checkers"] if c in chart.columns]))
    st.markdown("**Jobs rated** (the work behind each percentage)")
    st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch")
    st.markdown("**Actual sec per job**")
    sec = m["sec_per_job"].copy(); sec.columns = e.columns
    st.dataframe(sec.style.format("{:.0f}", na_rep="-"), width="stretch")


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by date verified)")
    period_tab("week", "%d-%b")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y")

with tab_f:
    st.subheader("Where verifications were not rated")
    st.dataframe(inv.status_counts(rows), width="stretch")
    st.markdown("- **Too long**: over the cap - usually a break or an interruption, not slow work.\n"
                "- **Overnight**: checked on a different day than it started.\n"
                "- **End before start / missing timestamp**: a timestamp problem worth a look.")
    ft = inv.flags_table(view)
    kinds = sorted(ft["status_text"].unique())
    pick = st.multiselect("Show", kinds, default=kinds, key="iv_flag_kinds")
    ft = ft[ft["status_text"].isin(pick)] if pick else ft.iloc[0:0]
    st.dataframe(ft.rename(columns={"name": "Checker", "drug_code": "Drug code", "verify_start": "Verify start", "checked_time": "Checked",
                                    "dur": "Seconds", "q_found": "Quantity found", "status_text": "Status"})
                 .style.format({"Verify start": lambda v: v.strftime("%d-%b-%y %H:%M"), "Checked": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M"),
                                "Seconds": "{:,.0f}", "Quantity found": "{:,.0f}"}, na_rep="-"), width="stretch", height=400)
    st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "inventory_review_flags.csv", "text/csv", key="iv_dl_flags")

with tab_how:
    st.markdown(f"""
### What this answers
How long does each person take to verify a drug code at its slot, compared with the team standard? 100% = at the standard, above = faster, below = slower.

### The process
The checker ticks the values in VERIFY (**verify_start**), then counts the slot and enters the quantity found (**checked_time**). The same person verifies and checks, and there is no walking or searching step.

### Time
Duration = `checked_time` - `verify_start`. Rated = a checker, both stamps, end after start, same calendar day, and not longer than {S.max_sec:g} seconds. Everything else is counted on Review flags.

### The standard
**{S.std_sec:g} seconds** ({S.std_sec / 60:.1f} min) per verification - the mean of rated same-day verifications, so the team sits near 100%.
Quantity found (from `tbt_differences`, matched on drug code and the checked time) was tested and does not explain the time, so there is no quantity term. It is shown for reference.

### How efficiency is calculated
Add the standard seconds and the actual seconds of a person's rated jobs, then divide once. People with fewer than {S.min_show} rated jobs show no percentage.

### Caution
Verification stamps only exist since 18 Aug 2026, so job counts are small. Use the percentages as a guide.
""")
    with st.expander("Data checks"):
        chk = inv.data_checks(rows)
        st.table(pd.DataFrame({"Check": list(chk.keys()), "Value": [f"{v:,}" if isinstance(v, int) else v for v in chk.values()]}))
