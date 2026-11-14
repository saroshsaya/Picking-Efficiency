"""Move (LT rack -> ST cubicle) efficiency page (runs inside app.py's navigation)."""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import move
from move import MoveSettings
from ui_common import cell_color, style_eff, settings_area, is_admin, data_checks_box

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("Move efficiency vs the standard")
settings_pop = settings_area(top_s)
if top_r.button("Refresh data", use_container_width=True, key="mv_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings
S = MoveSettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    st.markdown("**Standard time for a move = fixed seconds + seconds per unit x units moved**")
    S.std_fixed = st.number_input("Fixed seconds per move", value=float(config.MOVE_STD_FIXED), step=5.0, min_value=0.0, format="%.1f", key="mv_fixed",
                                  help="Time every move needs whatever its size: MOVE, the walk to the rack, the box, the cubicle, SUBMIT. From the move time study.")
    S.std_per_unit = st.number_input("Seconds per unit moved", value=float(config.MOVE_STD_PER_UNIT), step=0.1, min_value=0.0, format="%.2f", key="mv_unit",
                                     help="Extra seconds for each unit moved. Set to 0 for one flat time per move.")
    S.min_sec = st.number_input("Shortest time that can be rated (seconds)", value=float(config.MOVE_MIN_SEC), step=1.0, min_value=0.0, key="mv_min",
                                help="A shorter MOVE-to-SUBMIT is treated as the same moment and is not rated.")
    S.max_sec = st.number_input("Longest time that can be rated (seconds)", value=float(config.MOVE_MAX_SEC), step=60.0, min_value=60.0, key="mv_max",
                                help="A longer MOVE-to-SUBMIT is a break or a start left open: not rated.")
    S.slow_x = st.number_input("Flag as slow above (x the standard)", value=float(config.MOVE_SLOW_X), step=0.5, min_value=1.0, key="mv_slow",
                               help="Rated moves slower than this multiple of their standard are listed on Review flags. They stay in the efficiency figures.")
    S.min_show = st.number_input("Min rated moves to show a person's / the team's efficiency", value=1, step=1, min_value=0, key="mv_show",
                                 help="Below this the Overview shows a dash instead of a percentage.")
    S.min_show_period = st.number_input("Min rated moves to show a week / month efficiency", value=1, step=1, min_value=0, key="mv_showp",
                                        help="Weekly and Monthly tabs: fewer rated moves than this show a dash.")
    S.low_sample = st.number_input("Low-sample marker: rated moves below", value=15, step=1, min_value=0, key="mv_low",
                                   help="Cells built on fewer rated moves than this appear in grey italics.")
    st.caption("Time = MOVE pressed (move_start) to SUBMIT pressed (move_time), per drug code. Person = moved_by. Names come from config.py.")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
dev_csv = os.environ.get("DEV_MOVE_CSV")
live = db.secrets_available() and not dev_csv
if not live and not dev_csv:
    st.info("Move efficiency reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()
file_rows = move.load_csv(dev_csv) if dev_csv else None

chosen_window = None
with st.container(border=True):
    presets = (["Last 30 days", "Last 90 days", "Last 180 days", "This month", "Last month", "Custom"] if live else ["All data in file", "Custom"])
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date submitted)", presets, index=None, placeholder="Select a period", key="mv_preset")
    d_from = d_to = None
    if preset == "Custom":
        if live:
            lo, hi, v_from, v_to = None, None, today - dt.timedelta(days=29), today
        else:
            lo, hi = file_rows["move_time"].min().date(), file_rows["move_time"].max().date(); v_from, v_to = lo, hi
        d_from = p2.date_input("From", value=v_from, min_value=lo, max_value=hi, key="mv_from")
        d_to = p3.date_input("To", value=v_to, min_value=lo, max_value=hi, key="mv_to")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="mv_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All data in file":
        chosen_window = (file_rows["move_time"].min().date(), file_rows["move_time"].max().date())
    elif preset == "Last 30 days":
        chosen_window = (today - dt.timedelta(days=29), today)
    elif preset == "Last 90 days":
        chosen_window = (today - dt.timedelta(days=89), today)
    elif preset == "Last 180 days":
        chosen_window = (today - dt.timedelta(days=179), today)
    elif preset == "This month":
        chosen_window = (today.replace(day=1), today)
    elif preset == "Last month":
        _end = today.replace(day=1) - dt.timedelta(days=1)
        chosen_window = (_end.replace(day=1), _end)

if load_clicked and chosen_window:
    if chosen_window[1] < chosen_window[0]:
        st.error("'To' is before 'From'."); st.stop()
    st.session_state["mv_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("mv_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data. Moves can only be timed from "
            f"{config.MOVE_START_FROM} (the day the start stamp began), so a period before that shows counts but no times.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end, pad_seconds=0)
if live:
    try:
        raw = db.fetch_moves(s_ms, e_ms)
    except Exception as e:  # readable message, never the secrets
        st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
        st.stop()
else:
    raw = file_rows[(file_rows["move_time"] >= s_dt) & (file_rows["move_time"] < e_dt)]
if raw.empty:
    st.info(f"No moves submitted between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

moves = move.rate_moves(raw, S)
if moves.empty:
    st.info("Every move in this period belongs to a login that is left out in config.py (MOVE_EXCLUDED_LOGINS)."); st.stop()
names = move.person_order(moves)
st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}.")
chosen = st.multiselect("People", names, default=names, key="mv_chosen")
view = moves[moves["name"].isin(chosen)] if chosen else moves.iloc[0:0]
rated_view = view[view["rated"]]

# ---------------------------------------------------------------------- header
ov = move.overview_table(view, S)
tot = ov.loc["Team"]
n_all = len(view); n_timed = int(view["move_start"].notna().sum()); n_rated = int(view["rated"].sum())
c1, c3, _, _ = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="Standard seconds / actual seconds over rated moves. 100% = at the standard; above 100% = faster.")
c3.metric("Moves submitted", f"{n_all:,}", help=f"{n_timed:,} have a start time; {n_rated:,} could be rated")
st.caption("100% = at the standard for the units moved; above 100% = faster. Time = MOVE pressed to SUBMIT pressed, on the login that moved.")
if not S.std_is_set:
    st.info("The standard for a move is not set yet, so no efficiency percentage is shown. Enter the fixed seconds and the seconds per unit "
            "from the move time study in Settings (top right), or in config.py (MOVE_STD_FIXED, MOVE_STD_PER_UNIT).")
if n_all and n_timed < n_all:
    st.info(f"{n_all - n_timed:,} of {n_all:,} moves in this period have no start time, so they cannot be timed "
            f"(the start stamp exists from {config.MOVE_START_FROM}).")
if len(rated_view) >= 5 and rated_view["dur"].median() < 15:
    st.warning(f"Half of the rated moves take under {rated_view['dur'].median():.0f} seconds from MOVE to SUBMIT. Fetching a box from the rack and "
               "putting stock in the cubicle should take longer, so the start stamp may be set late (for example when the quantity screen opens) "
               "and not when MOVE is pressed. Check this before reading the percentages.")

tab_ov, tab_w, tab_m, tab_f, tab_how = st.tabs(["Overview", "Weekly", "Monthly", "Review flags", "How it works"])

# ------------------------------------------------------------------- overview
with tab_ov:
    st.subheader("Each person against the standard")
    show = pd.DataFrame({"Moves": ov["moves"].astype(int), "Average seconds": ov["avg_sec"], "Efficiency": ov["efficiency"]})
    disp = show.copy()
    disp["Average seconds"] = show["Average seconds"].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
    disp["Efficiency"] = show["Efficiency"].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")
    eff = show["Efficiency"]
    st.dataframe(disp.style.apply(lambda col: [cell_color(v) if pd.notna(v) and 0 < v < 5 else "" for v in eff], subset=["Efficiency"]), width="stretch")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "move_overview.csv", "text/csv", key="mv_dl_ov")
    if S.std_is_set:
        st.caption(f"Standard in use: {S.std_fixed:g} seconds per move + {S.std_per_unit:g} seconds per unit moved.")


def period_tab(period, fmt):
    m = move.period_matrices(view, S, period)
    if m["efficiency"].empty:
        st.info("No rated moves in this period."); return
    e, p = m["efficiency"].copy(), m["rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated moves; a dash = fewer than {S.min_show_period} (or no standard set)")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    if chart.drop(columns=[c for c in ["Team"] if c in chart.columns]).notna().any().any():
        st.line_chart(chart.drop(columns=[c for c in ["Team"] if c in chart.columns]))


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by the date the move was submitted)")
    period_tab("week", "%d-%b")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y")

# --------------------------------------------------------------- review flags
with tab_f:
    st.subheader("Where moves were not rated, and slow ones")
    st.dataframe(move.status_counts(view), width="stretch")
    st.markdown("- **No start time**: the move was made before the start stamp existed, so it cannot be timed.\n"
                "- **Start and submit too close**: the two stamps are the same moment, so no time can be measured.\n"
                "- **Too long**: a break, or the start was left open.\n"
                "- **Slow**: rated, but slower than the slow multiple in Settings. These stay in the efficiency figures.")
    with st.expander("Show the individual moves (not rated, and slow ones)"):
        ft = move.flags_table(view)
        st.dataframe(ft.rename(columns={"name": "Person", "dc": "Drug code", "qty": "Units", "move_start": "MOVE pressed", "move_time": "SUBMIT pressed",
                                        "dur": "Seconds", "std_sec": "Standard seconds", "review": "What to review"})
                     .style.format({"MOVE pressed": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M:%S"),
                                    "SUBMIT pressed": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M:%S"),
                                    "Units": "{:,.0f}", "Seconds": "{:,.0f}", "Standard seconds": "{:.0f}"}, na_rep="-"),
                     width="stretch", height=400, hide_index=True)
        st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "move_review_flags.csv", "text/csv", key="mv_dl_flags")

# ------------------------------------------------------------------ how it works
with tab_how:
    std_line = (f"**{S.std_fixed:g} seconds + {S.std_per_unit:g} seconds per unit moved**" if S.std_is_set else
                "**not set yet**. It will come from the move time study (fixed seconds per move plus seconds per unit moved)")
    st.markdown(f"""
### What this answers
How long does each person take to move one drug code from an LT rack to its ST cubicle, compared with the time it should take? 100% = at the standard, above = faster, below = slower.

### Time
**MOVE pressed (`move_start`) to SUBMIT pressed (`move_time`)**, for each drug code. That includes seeing the LT rack, fetching the box, putting the quantity in the cubicle and entering it.
The person is the login that moved it (`moved_by`). The size of a move is the units moved (`cumulative_change`).

### The standard
{std_line}.

### Rated moves
A move is rated when it has a start time, the submit is not before the start, the time is between **{S.min_sec:g} and {S.max_sec:g} seconds**, and the units moved are recorded.
Moves made before **{config.MOVE_START_FROM}** have no start time, so they are counted but cannot be timed. Everything not rated is counted on Review flags with the reason.

### How efficiency is calculated
Add the standard seconds and the actual seconds of a person's rated moves, then divide once (not an average of per-move percentages), so one slow move cannot hide in an average.
A person or the team shows a percentage only with at least {S.min_show} rated moves; a week or month cell needs at least {S.min_show_period}.

### Caution
The start stamp is new, so there are few timed moves so far. Read the number of rated moves beside each percentage. If most rated moves take only a few seconds,
the start may be stamped late rather than when MOVE is pressed, and the percentages would look far too high.
""")
    if is_admin():
        data_checks_box(move.data_checks(moves))
