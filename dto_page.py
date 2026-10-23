"""DTO (customer returns via courier) efficiency page (runs inside app.py's navigation)."""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import dto
from dto import DtoSettings
from ui_common import cell_color, style_eff

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("DTO efficiency vs the standard")
settings_pop = top_s.popover("☰ Settings", use_container_width=True)
if top_r.button("Refresh data", use_container_width=True, key="dt_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings
S = DtoSettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    S.std_sec = st.number_input("Standard seconds per DTO", value=float(config.DTO_STD_SEC), step=1.0, min_value=1.0, key="dt_std",
                                help="From the DTO time study: the average Start to End of 17 timed DTOs. It includes a DTO with an issue and the slowest one, so it leaves some leeway.")
    S.allowance = st.number_input("Allowance on the standard (%)", value=0.0, step=5.0, min_value=0.0, key="dt_allow",
                                  help="Optional extra time on top of the standard. Leave at 0: the leeway is already in the standard. 10 = every standard time 10% longer.") / 100
    S.min_sec = st.number_input("Shortest time that can be rated (seconds)", value=float(config.DTO_MIN_SEC), step=1.0, min_value=0.0, key="dt_min",
                                help="A shorter Start to End is treated as the same moment (stamps pressed or recorded together) and is not rated.")
    S.max_sec = st.number_input("Longest time that can be rated (seconds)", value=float(config.DTO_MAX_SEC), step=60.0, min_value=60.0, key="dt_max",
                                help="A longer Start to End means the start or the end was missed, or a break: not rated.")
    S.slow_x = st.number_input("Flag as slow above (x the standard)", value=float(config.DTO_SLOW_X), step=0.5, min_value=1.0, key="dt_slow",
                               help="Rated DTOs slower than this multiple of the standard are listed on Review flags. They stay in the efficiency figures.")
    S.min_show = st.number_input("Min rated DTOs to show a person's / the team's efficiency", value=20, step=1, min_value=0, key="dt_show",
                                 help="Below this the Overview shows a dash instead of a percentage.")
    S.min_show_period = st.number_input("Min rated DTOs to show a week / month efficiency", value=5, step=1, min_value=0, key="dt_showp",
                                        help="Weekly and Monthly tabs: fewer rated DTOs than this show a dash.")
    S.low_sample = st.number_input("Low-sample marker: rated DTOs below", value=15, step=1, min_value=0, key="dt_low",
                                   help="Cells built on fewer rated DTOs than this appear in grey italics.")
    st.caption("Time = Start Return Processing (spt) to Return Handled (trh). Person = the login that accepted the DTO. Names come from config.py.")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
dev_csv = os.environ.get("DEV_DTO_CSV")
live = db.secrets_available() and not dev_csv
if not live and not dev_csv:
    st.info("DTO efficiency reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()
file_rows = None
if dev_csv:
    # same path as the live data: the stamps are read as text (epoch values) and converted by dto.prepare
    file_rows = dto.prepare(pd.read_csv(dev_csv, dtype=str))

chosen_window = None
with st.container(border=True):
    presets = ["Last 30 days", "Last 90 days", "All DTOs", "This month", "Last month", "Custom"]
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date the DTO ended)", presets, index=None, placeholder="Select a period", key="dt_preset")
    d_from = d_to = None
    if preset == "Custom":
        d_from = p2.date_input("From", value=today - dt.timedelta(days=29), key="dt_from")
        d_to = p3.date_input("To", value=today, key="dt_to")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="dt_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All DTOs":
        chosen_window = (dt.date(2000, 1, 1), today)
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
    st.session_state["dt_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("dt_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data. DTOs are few (about 20 a week), so a longer period gives steadier numbers.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")

if live:
    try:
        raw = db.fetch_dto()
    except Exception as e:  # readable message, never the secrets
        st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
        st.stop()
else:
    raw = file_rows
every = dto.rate_dtos(raw, S)
s_dt = pd.Timestamp(start); e_dt = pd.Timestamp(end) + pd.Timedelta(days=1)
window_rows = every[(every["date"] >= s_dt) & (every["date"] < e_dt)]
undated = int(every["date"].isna().sum())
if window_rows.empty:
    st.info(f"No DTOs ended or started between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

dtos = window_rows
names = dto.person_order(dtos)
st.caption(f"{start:%d %b %Y} to {end:%d %b %Y} (IST, by the date the DTO ended) - {len(dtos):,} DTOs read. Weeks and months at the edges may be partial."
           + (f" {undated:,} DTOs in the table have not been accepted yet and are left out." if undated else ""))
if not names:
    st.warning("No DTO in this period has been finished. See Review flags / How it works.")
chosen = st.multiselect("People", names, default=names, key="dt_chosen")
view = dtos[dtos["name"].isin(chosen) | ~dtos["finished"]] if chosen else dtos[~dtos["finished"]]
rated_view = view[view["rated"]]

# ---------------------------------------------------------------------- header
ov = dto.overview_table(view, S)
tot = ov.loc["Team"] if "Team" in ov.index else None
fin_n = int(view["finished"].sum()); rated_n = int(view["rated"].sum()); same_n = int((view["status_text"] == dto.S_SAME).sum())
c1, c2, c3, c4 = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if tot is None or pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="Standard seconds / actual seconds over rated DTOs. The standard is the time-study average, which includes a DTO with an issue, so compare people, weeks and months as well as the team figure.")
c2.metric("Standard per DTO", f"{S.std_sec * (1 + S.allowance):.0f} s")
c3.metric("DTOs finished", f"{fin_n:,}", help=f"{rated_n:,} rated")
avg = rated_view["dur"].mean() if len(rated_view) else np.nan
c4.metric("Average time of rated DTOs", "-" if np.isnan(avg) else f"{avg:.0f} s",
          help="Compare with the standard: if it is far from it, the start and end stamps may not be the clicks that were timed in the study.")
st.caption("100% = at the standard; above 100% = faster. Time = Start Return Processing to Return Handled, on the login that accepted the DTO.")
if fin_n and same_n / fin_n >= 0.3:
    st.warning(f"For {same_n:,} of {fin_n:,} finished DTOs the start and the end are at the same recorded time, so they cannot be rated. "
               "Check that the database values are read to the second (see How it works).")

tab_ov, tab_w, tab_m, tab_f, tab_how = st.tabs(["Overview", "Weekly", "Monthly", "Review flags", "How it works"])

# ------------------------------------------------------------------- overview
with tab_ov:
    st.subheader("Each person against the standard")
    show = pd.DataFrame({
        "Finished DTOs": ov["finished"].astype(int), "Rated": ov["rated"].astype(int), "Share rated": ov["share_rated"],
        "Average seconds": ov["avg_sec"], "Efficiency": ov["efficiency"], "Note": ov["note"],
    })
    disp = show.copy()
    disp["Share rated"] = show["Share rated"].map(lambda v: "-" if pd.isna(v) else f"{v:.0%}")
    disp["Average seconds"] = show["Average seconds"].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
    disp["Efficiency"] = show["Efficiency"].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")
    eff = show["Efficiency"]
    st.dataframe(disp.style.apply(lambda col: [cell_color(v) if pd.notna(v) and 0 < v < 5 else "" for v in eff], subset=["Efficiency"]), width="stretch")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "dto_overview.csv", "text/csv", key="dt_dl_ov")
    st.caption(f"Standard in use: {S.std_sec * (1 + S.allowance):.0f} seconds per DTO "
               f"(time study average of 17 DTOs, 17-22 Oct 2026). Average rated time in this period: "
               f"{'-' if np.isnan(avg) else f'{avg:,.0f} seconds'}.")


def period_tab(period, fmt):
    m = dto.period_matrices(view, S, period)
    if m["efficiency"].empty:
        st.info("No rated DTOs in this period."); return
    e, p = m["efficiency"].copy(), m["rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated DTOs; a dash = fewer than {S.min_show_period}")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    st.line_chart(chart.drop(columns=[c for c in ["Team"] if c in chart.columns]))
    st.markdown("**DTOs rated** (the work behind each percentage)")
    st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch")
    st.markdown("**Average seconds per DTO**")
    mn = m["avg_sec"].copy(); mn.columns = e.columns
    st.dataframe(mn.style.format("{:,.0f}", na_rep="-"), width="stretch")


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by the date the DTO ended)")
    period_tab("week", "%d-%b")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y")

# --------------------------------------------------------------- review flags
with tab_f:
    st.subheader("Where DTOs were not rated, and slow ones")
    st.dataframe(dto.status_counts(dtos), width="stretch")
    st.markdown("- **Start and end at the same time**: the two stamps are the same moment, so no time can be measured.\n"
                "- **Too long**: the start or the end may have been missed, or the DTO spans a break or a night.\n"
                "- **No start or end time yet**: not started or not finished.\n"
                "- **Slow**: rated, but slower than the slow multiple in Settings. These stay in the efficiency figures.")
    ft = dto.flags_table(view)
    st.dataframe(ft.rename(columns={"name": "Person", "awb": "AWB", "spt": "Start", "trh": "End", "dur": "Seconds",
                                    "std_sec": "Standard seconds", "review": "What to review"})
                 .style.format({"Start": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M:%S"),
                                "End": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M:%S"),
                                "Seconds": "{:,.0f}", "Standard seconds": "{:.0f}"}, na_rep="-"),
                 width="stretch", height=400, hide_index=True)
    st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "dto_review_flags.csv", "text/csv", key="dt_dl_flags")

# ------------------------------------------------------------------ how it works
with tab_how:
    st.markdown(f"""
### What this answers
How long does each person take to process a returned parcel (DTO), compared with the time a DTO should take? 100% = at the standard, above = faster, below = slower.

### Time
**Start Return Processing (`spt`) to Return Handled (`trh`).** Courier time is not used. Restocking after Return Handled is a separate activity and is not counted.
The person is the login that accepted the DTO (`accepted_by`).

### The standard
**{S.std_sec * (1 + S.allowance):.0f} seconds per DTO**, one flat figure. It is the average Start to End of the 17 DTOs timed in the DTO time study (17-22 Oct 2026, Akash and Sunny).
That average includes the one DTO with an issue (photos added - adding photos is part of the DTO process) and the slowest DTO, so it leaves some leeway:
when everything is fine the executive shows as efficient, and a DTO with an issue costs little. No separate allowance is given for issue DTOs.

The aim is for the standard to count the total items and the medicines (SKUs) in the DTO, because the executive counts the medicines returned and does a quick QC.
The table has no item counts yet, so one flat standard is used. In the time study the number of units explained part of the time (about 92 seconds plus 8.7 seconds per unit);
the number of medicines added nothing measurable, but only 5 of the 17 DTOs had more than one.

### Rated DTOs
A DTO is rated when it has both stamps, the end is not before the start, and the time is between **{S.min_sec:g} and {S.max_sec:g} seconds**. All other DTOs are counted on Review flags with the reason.

### How efficiency is calculated
Add the standard seconds and the actual seconds of a person's rated DTOs, then divide once (not an average of per-DTO percentages), so one slow DTO cannot hide in an average of percentages.
A person or the team shows a percentage only with at least {S.min_show} rated DTOs; a week or month cell needs at least {S.min_show_period}.

### The database values
The stored times are a mix of epoch milliseconds (13 digits, older module) and epoch seconds (10 digits, newer module). They are read as text and converted by length, so both are exact to the second.
If many DTOs show the same start and end, check the Data checks below: **start equals the Accept Return time** being high would mean Start Return Processing is not a separate click.

### Caution
DTOs are few, so use the percentages as a guide and read the number of rated DTOs beside each one. The average rated time in the header should be near the standard; if it is far off, the stamps may not match the clicks that were timed.
""")
    with st.expander("Data checks"):
        chk = dto.data_checks(dtos)
        st.table(pd.DataFrame({"Check": list(chk.keys()), "Value": [f"{v:,}" if isinstance(v, int) else v for v in chk.values()]}))
