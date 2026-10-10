"""Picking efficiency page (runs inside app.py's navigation)."""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import efficiency as eff
from efficiency import Settings
from ui_common import cell_color, style_eff, fmt_cols

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("Picking efficiency vs the time-study average")
settings_pop = top_s.popover("☰ Settings", use_container_width=True)
if top_r.button("Refresh data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings (like the Excel 'Settings' sheet)
S = Settings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    S.benchmark_sec = st.number_input("Recorded average picking rate (sec/item)", value=config.STUDY_PICKING_SEC_PER_ITEM, step=0.01, format="%.2f",
                                      help="From the time study (Overall Picking Rate, 20 clean cycles). Used by the old pieces-only model; shown as a reference otherwise.")
    S.allowance = st.number_input("Allowance on average (%)", value=0.0, step=1.0, min_value=0.0,
                                  help="Optional extra time allowed for fatigue or small interruptions. 0% = compare with the pure recorded average.") / 100
    S.model = st.radio("Standard model", [2, 1], format_func=lambda x: "Pieces + distinct SKUs (recommended)" if x == 2 else "Pieces only (old)", key="pick_model",
                       help="A list's time depends on walking to each SKU as well as on picking the pieces. Model 2 allows for both, so lists with fewer pieces per SKU (evenings) are not penalised.")
    if S.model == 2:
        S.b_piece = st.number_input("Seconds per piece (b)", value=config.PICK_STD_B_PIECE, step=0.05, format="%.2f", key="pick_b",
                                    help="Extra seconds for each piece picked. Fitted on real lists.")
        S.c_sku = st.number_input("Seconds per distinct SKU (c)", value=config.PICK_STD_C_SKU, step=0.5, format="%.2f", key="pick_c",
                                  help="Seconds for each different drug on the list (walking to the slot, finding it, scanning it). Fitted on real lists.")
        _lvl = st.radio("Standard level", ["Typical actual pace (team = 100%)", "Time-study pace"], key="pick_level_mode",
                        help="Typical actual pace is scaled so the team as a whole sits near 100%, as in Packing: above 100% = faster than the team's usual pace. "
                             "Time-study pace is stricter: it reproduces your 20 recorded study lists, so everyone sits well below 100%.")
        S.level_mode = "study" if _lvl.startswith("Time") else "typical"
        st.caption(f"Standard for a typical list (224 pieces, 47 SKUs): **{S.std_seconds(224, 47) / 60:.1f} min** (scale {S.k:.2f})")
    else:
        st.caption(f"Benchmark used for comparison: **{S.bench:.2f} sec/item**")
    fit_note = st.empty()
    S.basis = st.radio("Picking time basis", [1, 2], format_func=lambda x: "1: creation to last scan" if x == 1 else "2: first scan to last scan",
                       help="1 = from make list creation to last scan (how the study was timed). 2 = first scan to last scan for every list.")
    S.min_pieces = st.number_input("Min pieces for a list to be rated", value=60, step=5, min_value=0,
                                   help="Lists with fewer pieces are not rated (set-up time dominates). Their pieces still show as work picked.")
    S.partial_share = st.slider("Partial list: share of lines picked below", 0.0, 1.0, 0.90, 0.05,
                                help="If fewer lines than this were scanned, the list is flagged as partial and timed from its first scan.")
    S.long_wait_min = st.number_input("Long wait: minutes from creation to first scan above", value=10.0, step=1.0,
                                      help="Lists that waited longer than this before the first scan are flagged for review and timed from their first scan.")
    lead_auto = st.checkbox("Lead-in allowance: use the median of normal lists", value=True,
                            help="Added to lists timed from their first scan so they compare fairly with normal lists.")
    lead_manual = st.number_input("Lead-in allowance if not automatic (min)", value=1.0, step=0.5)
    lead_note = st.empty()
    S.max_minutes = st.number_input("Max picking minutes per list", value=90.0, step=5.0,
                                    help="Lists longer than this are flagged and not rated (a list left open distorts pace).")
    S.min_pieces_show = st.number_input("Min pieces to show an efficiency cell", value=0, step=10, min_value=0,
                                        help="0 = show a percentage whenever there is any rated work. Raise it to hide results built on very little work.")
    S.low_sample = st.number_input("Low-sample marker: pieces below", value=150, step=10, min_value=0,
                                   help="Cells built on fewer rated pieces than this appear in grey italics.")
    S.max_codes = st.number_input("Max distinct drug codes per make list", value=48, step=1, min_value=1,
                                  help="A make list holds at most 48 different drugs. A list above this is flagged (probably two lists created together).")
    S.merge_seconds = st.number_input("Merge lines created within (seconds)", value=2.0, step=1.0, min_value=0.0,
                                      help="Lines of one login created this close together are one make list. Use 0 for CSVs saved with minute-only times.")
    st.caption("Executive names come from config.py.")

# ------------------------------------------------------------ data source + date window
source_opts = (["Live database"] if db.secrets_available() else []) + ["Upload CSV"]
dev_csv = os.environ.get("DEV_CSV_PATH")
if dev_csv:
    source_opts.insert(0, "Dev CSV (env)")
today = dt.datetime.now(db.IST).date()

file_lines = None
chosen_window = None            # (start, end) of the period currently picked in the menu
with st.container(border=True):
    cs, cf = st.columns([1.2, 5])
    source = cs.radio("Data source", source_opts, horizontal=False) if len(source_opts) > 1 else source_opts[0]
    live = source == "Live database"
    if source == "Dev CSV (env)":
        file_lines = db.load_csv(dev_csv)
    elif source == "Upload CSV":
        up = cf.file_uploader("CSV written by extract_data.py", type="csv")
        if up is not None:
            file_lines = db.load_csv(up)
        else:
            st.info("Upload the CSV, or add the database secrets (see README) to read live data."); st.stop()

    presets = (["Last 7 days", "Last 30 days", "Last 90 days", "This month", "Last month", "Custom"] if live
               else ["All data in file", "Custom"])
    p1, p2, p3, p4 = cf.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period", presets, index=None, placeholder="Select a period", key=f"preset_{source}")
    d_from = d_to = None
    if preset == "Custom":                       # the two date boxes appear only for Custom
        if live:
            lo, hi, v_from, v_to = None, None, today - dt.timedelta(days=29), today
        else:
            lo = file_lines["make_list_created_at"].min().date(); hi = file_lines["make_list_created_at"].max().date()
            v_from, v_to = lo, hi
        d_from = p2.date_input("From", value=v_from, min_value=lo, max_value=hi, key=f"from_{source}")
        d_to = p3.date_input("To", value=v_to, min_value=lo, max_value=hi, key=f"to_{source}")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None)

    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All data in file":
        chosen_window = (file_lines["make_list_created_at"].min().date(), file_lines["make_list_created_at"].max().date())
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

# nothing is read until Load is pressed; the loaded period then stays while settings / tabs change
if load_clicked and chosen_window:
    if chosen_window[1] < chosen_window[0]:
        st.error("'To' is before 'From'."); st.stop()
    st.session_state["loaded"] = {"source": source, "start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("loaded")
if not loaded or loaded["source"] != source:
    st.info("Choose a period above and press **Load** to read the data.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")
if live and (end - start).days > 120:
    st.warning("Large date range - this reads a lot of rows from the database and may take a while.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end)
if live:
    try:
        lines = db.fetch_lines(s_ms, e_ms)
    except Exception as e:  # readable message, never the secrets
        st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
        st.stop()
else:
    lines = file_lines[(file_lines["make_list_created_at"] >= s_dt - dt.timedelta(seconds=db.PAD_SECONDS)) &
                       (file_lines["make_list_created_at"] < e_dt + dt.timedelta(seconds=db.PAD_SECONDS))]
if lines.empty:
    st.info(f"No make lists between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

lists, lines_id = st.cache_data(eff.build_lists)(lines, S.merge_seconds)
lists = lists[(lists["start"] >= s_dt) & (lists["start"] < e_dt)]
if lists.empty:
    st.info(f"No make lists between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()
auto_lead = round(eff.median_lead_in(lists, S), 1)
S.lead_allowance_min = auto_lead if lead_auto else lead_manual
lead_note.caption(f"Median lead-in of normal lists in this period: {auto_lead} min. In use: **{S.lead_allowance_min:g} min**")

rated = eff.rate_lists(lists, S)
_fit = eff.fit_shape(rated)
if _fit:
    fit_note.caption(f"Fitted on this period ({_fit[2]:,} rated lists, within each person): {_fit[0]:.2f} s per piece, {_fit[1]:.2f} s per SKU, "
                     f"scale for team = 100%: {_fit[4]:.2f} (R2 {_fit[3]:.2f}). Compare with the values above to see whether the standard needs refreshing.")
all_execs = eff.exec_order(rated)
st.caption(f"{start:%d %b %Y} to {end:%d %b %Y} (IST, by list creation) - {len(lines):,} lines read, {len(rated):,} make lists. "
           "Weeks and months at the edges of the period may be partial.")
chosen = st.multiselect("Executives", all_execs, default=all_execs)
view = rated[rated["exec"].isin(chosen)]
if view.empty:
    st.info("Select at least one executive."); st.stop()

# ---------------------------------------------------------------------- header
ov = eff.overview_table(view, S)
tot = ov.loc["All executives"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}")
if S.model == 2:
    c2.metric("Standard for a typical list", f"{S.std_seconds(224, 47) / 60:.1f} min", help="224 pieces, 47 distinct SKUs - the average list. Standard seconds = scale x (b x pieces + c x SKUs).")
else:
    c2.metric("Benchmark (sec/item)", f"{S.bench:.2f}", help="Recorded average x (1 + allowance)")
c3.metric("Pieces picked", f"{int(tot['pieces_all']):,}", help=f"{int(tot['pieces_rated']):,} rated, {int(tot['pieces_not_rated']):,} not rated")
c4.metric("Lists flagged for review", f"{int(tot['lists_flagged']):,}")
st.caption(("100% = at the standard for the list's pieces and SKUs; above 100% = faster. " if S.model == 2 else "100% = at the recorded average; above 100% = faster. ")
           + "Only picking is compared - assembly has no usable timestamps.")

tab_ov, tab_w, tab_m, tab_d, tab_f, tab_how = st.tabs(["Recorded vs actual", "Weekly", "Monthly", "Daily", "Review flags", "How it works"])

# ------------------------------------------------------------------- overview
with tab_ov:
    st.subheader("Recorded average vs each executive's actual pace")
    a, b = st.columns(2)
    a.metric("Picking - recorded average", f"{config.STUDY_PICKING_SEC_PER_ITEM:.2f} sec/item",
             help=f"{config.STUDY_CYCLES} clean study cycles, {config.STUDY_ITEMS:,} items")
    b.metric("Assembly - recorded average (reference only)", f"{config.STUDY_ASSEMBLY_SEC_PER_ITEM:.2f} sec/item")
    t = ov.copy()
    t.insert(0, "Notes", [config.EXEC_NOTES.get(i, "") for i in t.index])
    show = pd.DataFrame({
        "Lists picked": t["lists_picked"].astype(int), "Pieces picked (all)": t["pieces_all"].astype(int),
        "Lists rated": t["lists_rated"].astype(int), "Pieces rated": t["pieces_rated"].astype(int),
        "Pieces not rated": t["pieces_not_rated"].astype(int), "Actual picking min (rated)": t["minutes_rated"],
        "Actual sec/item": t["sec_per_item"], "Recorded average sec/item (reference)": t["benchmark_sec"],
        "Difference vs study (sec/item, + = slower)": t["difference_sec"], "Efficiency": t["efficiency"],
        "Above / below average": t["verdict"], "Lists flagged": t["lists_flagged"].astype(int), "Notes": t["Notes"],
    })
    st.dataframe(
        show.style.format({"Actual picking min (rated)": "{:,.0f}", "Actual sec/item": "{:.2f}", "Recorded average sec/item (reference)": "{:.2f}",
                           "Difference vs study (sec/item, + = slower)": "{:+.2f}", "Efficiency": "{:.1%}", "Pieces picked (all)": "{:,}",
                           "Pieces rated": "{:,}", "Pieces not rated": "{:,}"}, na_rep="-")
        .map(lambda v: cell_color(v) if isinstance(v, float) and not np.isnan(v) and v < 5 and v > 0 else "", subset=["Efficiency"]),
        width="stretch")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "recorded_vs_actual.csv", "text/csv")
    st.caption("'Pieces not rated' is real work on lists that were too small or too long: shown here but kept out of the percentage.")


def period_tab(period, label, fmt):
    m = eff.period_matrices(view, S, period)
    e, p = m["efficiency"].copy(), m["pieces_rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs recorded average** - grey italics = fewer than {S.low_sample} rated pieces")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    st.line_chart(chart.drop(columns=[c for c in ["All executives"] if c in chart.columns]))
    st.markdown("**Pieces rated** (the work behind each percentage)")
    st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch")
    st.markdown("**Actual sec/item**")
    sec = m["sec_per_item"].copy(); sec.columns = e.columns
    st.dataframe(sec.style.format("{:.2f}", na_rep="-"), width="stretch")
    st.markdown("**Pieces picked, all lists (including lists not rated)**")
    al = m["pieces_all"].copy(); al.columns = e.columns
    st.dataframe(al.style.format("{:,.0f}", na_rep="-"), width="stretch")


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by list creation date)")
    period_tab("week", "Week starting", "%d-%b")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "Month", "%b-%y")
with tab_d:
    st.subheader("Daily")
    m = eff.period_matrices(view, S, "date")
    e = m["efficiency"].T; p = m["pieces_rated"].T
    e.index = [i.strftime("%a %d-%b") if hasattr(i, "strftime") else i for i in e.index]; p.index = e.index
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch", height=600)
    pick_cols = st.multiselect("Show in chart", list(e.columns), default=["All executives"], key="daily_chart_cols")
    if pick_cols:
        st.line_chart(e[pick_cols])
    with st.expander("Pieces rated per day"):
        st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch", height=500)

# --------------------------------------------------------------- review flags
with tab_f:
    st.subheader("Lists worth investigating")
    st.markdown(
        "- **Long wait**: the list sat before its first scan - slow to appear on the device, or the executive started late. The data cannot say which.\n"
        "- **Partial list**: fewer lines scanned than the threshold - out of stock, lines skipped, or a duplicate list created at the same moment and never worked.\n"
        f"- **Over {S.max_codes} drug codes**: probably two lists created together and merged.\n"
        "- **Too long**: ran past the maximum minutes (maybe left open); shown but not rated.")
    ft = eff.flags_table(view)
    kinds = st.multiselect("Show flags", ["Long wait", "Partial list", "Over", "Too long"], default=["Long wait", "Partial list", "Over", "Too long"])
    if kinds:
        ft = ft[ft["flags"].apply(lambda s: any(k in s for k in kinds))]
    st.dataframe(ft.rename(columns={"ml_id": "ML ID", "exec": "Executive", "start": "List created", "lead_in": "Wait before first scan (min)",
                                    "first": "First scan", "last": "Last scan", "lines": "Lines", "picked_lines": "Lines picked",
                                    "share": "Share picked", "pieces": "Pieces", "distinct_codes": "Distinct drug codes",
                                    "flags": "Flags", "status": "Status"})
                 .style.format({"Wait before first scan (min)": "{:.0f}", "Share picked": "{:.0%}", "Pieces": "{:,}"}, na_rep="-"),
                 width="stretch", height=450)
    st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "review_flags.csv", "text/csv")
    st.markdown("---")
    st.subheader("Look inside a list")
    ids = ft["ml_id"].tolist() or rated.loc[rated["picked"], "ml_id"].tolist()[:200]
    pick = st.selectbox("ML ID", ids) if ids else None
    if pick:
        r = rated[rated["ml_id"] == pick].iloc[0]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Executive", r["exec"]); k2.metric("Lines picked", f"{int(r['picked_lines'])}/{int(r['lines'])}")
        k3.metric("Pieces", f"{int(r['pieces']):,}")
        k4.metric("Minutes used", "-" if pd.isna(r["minutes"]) else f"{r['minutes']:.0f}")
        st.write(f"Status: **{r['status']}**" + (f"  |  Flags: {r['flags']}" if r["flags"] else ""))
        st.dataframe(lines_id[lines_id["ml_id"] == pick].drop(columns=["ml_id"]), width="stretch", height=300)

# ------------------------------------------------------------------ how it works
with tab_how:
    st.markdown(f"""
### What this answers
How fast does each executive actually pick, compared with the time a list of that size should take? 100% = exactly at the standard, above = faster, below = slower.
{"The standard allows for **pieces and distinct SKUs**: a list's time is mostly walking to each SKU (about " + f"{S.c_sku * S.k:.0f}" + " s per SKU) plus a little per piece (about " + f"{S.b_piece * S.k:.1f}" + " s). So a morning list with many pieces per SKU and an evening list with few are both judged fairly." if S.model == 2 else "The standard is the time-study average of **" + f"{config.STUDY_PICKING_SEC_PER_ITEM}" + " sec/item** (pieces only)."}

### Words used
- **Make list** - a set of drugs to pick (at most {S.max_codes} different drug codes). Lines of one login created within {S.merge_seconds:g} seconds are one list.
- **Line / piece** - one drug on one order / one unit picked. A line with no scan time (`updated_at`) was not picked.
- **Lead-in** - minutes from list creation to its first scan (median of normal lists: {auto_lead} min).
- **Rated list** - at least {S.min_pieces} pieces and not longer than {S.max_minutes:g} minutes.

### How a list is timed
- Normal list: creation to last scan (this is how the stopwatch time study was timed).
- Long wait (first scan more than {S.long_wait_min:g} min after creation) or partial list (under {S.partial_share:.0%} of lines picked):
  flagged, and timed from the first scan plus the lead-in allowance, so the executive is not charged for time the data cannot explain.

### How efficiency is calculated
For one executive and one period, add up the standard minutes and the actual minutes of the rated lists, then divide once:
`efficiency = standard minutes / actual minutes`. Standard seconds for a list = **scale x ({S.b_piece:g} x pieces + {S.c_sku:g} x distinct SKUs picked)** (scale {S.k:.2f}; 1.00 = typical actual pace)
in the pieces + SKUs model, or pieces x {S.bench:.2f} in the old model.
It is **not** an average of per-list percentages, so bigger lists count for more.

### Not used
Assembly (no usable timestamps) - the recorded {config.STUDY_ASSEMBLY_SEC_PER_ITEM} sec/item is reference only.
`subtraction_time` (when stock was subtracted) is checked below but not used.
""")
    with st.expander("Data checks"):
        chk = eff.data_checks(lines, rated)
        st.table(pd.DataFrame({"Check": list(chk.keys()), "Value": [f"{v:,}" if isinstance(v, int) else v for v in chk.values()]}))
