"""Sample creation efficiency page (runs inside app.py's navigation)."""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import sample as sm
from sample import SampleSettings
from ui_common import cell_color, style_eff

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("Sample creation efficiency vs the standard")
settings_pop = top_s.popover("☰ Settings", use_container_width=True)
if top_r.button("Refresh data", use_container_width=True, key="sm_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings
S = SampleSettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    st.markdown("**Standard time for a sample = a + b x items** (SKU count is not used)")
    S.a = st.number_input("a: fixed seconds per sample", value=config.SAMPLE_STD_A, step=30.0, format="%.2f", key="sm_a",
                          help="Time every sample needs whatever its size (slips, collecting, boxing, sealing). Fitted on same-day samples.")
    S.b = st.number_input("b: seconds per item", value=config.SAMPLE_STD_B, step=0.5, format="%.2f", key="sm_b",
                          help="Extra seconds for each item - each one is blacked out by hand.")
    S.exclude_overnight = st.checkbox("Do not rate overnight samples", value=True, key="sm_night",
                                      help="A sample whose shipping_time is on a different day than created_at is not rated (the process cannot run overnight).")
    S.include_outside_team = st.checkbox("Also rate logins outside the team list", value=False, key="sm_team",
                                         help="Off = the logins listed in config.py (SAMPLE_EXCLUDED_LOGINS) are not rated, as in the Excel workbook.")
    S.min_show = st.number_input("Min rated samples to show an efficiency cell", value=0, step=1, min_value=0, key="sm_show",
                                 help="0 = show a percentage whenever there is any rated work.")
    S.min_show_period = st.number_input("Min rated samples to show a week / month efficiency", value=3, step=1, min_value=0, key="sm_showp",
                                        help="Weekly and Monthly tabs: fewer rated samples than this show a dash.")
    S.low_sample = st.number_input("Low-sample marker: rated samples below", value=5, step=1, min_value=0, key="sm_low",
                                   help="Cells built on fewer rated samples than this appear in grey italics.")
    st.caption("Duration = created_at to shipping_time. Person = the packer. Names come from config.py.")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
dev_csv = os.environ.get("DEV_SAMPLE_CSV")
live = db.secrets_available() and not dev_csv
if not live and not dev_csv:
    st.info("Sample creation reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()
file_rows = None
if dev_csv:
    file_rows = pd.read_csv(dev_csv)
    for c in ["created_at", "shipping_time"]:
        file_rows[c] = pd.to_datetime(file_rows[c], errors="coerce")

chosen_window = None
with st.container(border=True):
    presets = (["Last 30 days", "Last 90 days", "Last 180 days", "This month", "Last month", "Custom"] if live else ["All data in file", "Custom"])
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date created)", presets, index=None, placeholder="Select a period", key="sm_preset")
    d_from = d_to = None
    if preset == "Custom":
        if live:
            lo, hi, v_from, v_to = None, None, today - dt.timedelta(days=89), today
        else:
            lo, hi = file_rows["created_at"].min().date(), file_rows["created_at"].max().date(); v_from, v_to = lo, hi
        d_from = p2.date_input("From", value=v_from, min_value=lo, max_value=hi, key="sm_from")
        d_to = p3.date_input("To", value=v_to, min_value=lo, max_value=hi, key="sm_to")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="sm_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All data in file":
        chosen_window = (file_rows["created_at"].min().date(), file_rows["created_at"].max().date())
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
    st.session_state["sm_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("sm_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data. Samples are few, so a long period (90-180 days or more) gives steadier numbers.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end, pad_seconds=0)
if live:
    try:
        raw = db.fetch_samples(s_ms, e_ms)
    except Exception as e:  # readable message, never the secrets
        st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
        st.stop()
else:
    raw = file_rows[(file_rows["created_at"] >= s_dt) & (file_rows["created_at"] < e_dt)]
if raw.empty:
    st.info(f"No samples created between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

samples = sm.rate_samples(raw, S)
rated_names = sm.packer_order(samples)
st.caption(f"{start:%d %b %Y} to {end:%d %b %Y} (IST, by date created) - {len(raw):,} samples read. Weeks and months at the edges may be partial.")
if not rated_names:
    st.warning("No sample in this period could be rated. See Review flags / How it works for the reasons.")
chosen = st.multiselect("Packers", rated_names, default=rated_names, key="sm_chosen")
view = samples[samples["name"].isin(chosen) | ~samples["rated"]] if chosen else samples[~samples["rated"]]
rated_view = view[view["rated"]]

# ---------------------------------------------------------------------- header
ov = sm.overview_table(view, S)
tot = ov.loc["All packers"] if "All packers" in ov.index else None
c1, c2, c3, c4 = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if tot is None or pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="Standard seconds / actual seconds over rated samples. The standard was fitted on past samples, so the team sits near 100% by design.")
c2.metric("Standard for a typical sample (70 items)", f"{(S.a + S.b * 70) / 60:.0f} min")
c3.metric("Samples created", f"{len(samples):,}", help=f"{int(samples['rated'].sum()):,} rated")
med = rated_view["dur"].median() / 60 if len(rated_view) else np.nan
c4.metric("Median time of rated samples", "-" if np.isnan(med) else f"{med:.0f} min")
st.caption("100% = at the standard for the sample's item count; above 100% = faster. Time = created_at to shipping_time, on the packer.")

tab_ov, tab_w, tab_m, tab_f, tab_how = st.tabs(["Overview", "Weekly", "Monthly", "Review flags", "How it works"])

# ------------------------------------------------------------------- overview
with tab_ov:
    st.subheader("Each packer against the standard")
    show = pd.DataFrame({
        "Samples rated": ov["samples_rated"].astype(int), "Avg minutes per sample": ov["min_per_sample"],
        "Items rated": ov["items_rated"].astype(int), "Actual sec/item": ov["sec_per_item"],
        "Efficiency": ov["efficiency"], "Above / below team": ov["verdict"],
    })
    disp = show.copy()
    for c, fmt in {"Avg minutes per sample": "{:.1f}", "Actual sec/item": "{:.1f}"}.items():
        disp[c] = show[c].map(lambda v, fmt=fmt: "-" if pd.isna(v) else fmt.format(v))
    disp["Efficiency"] = show["Efficiency"].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")
    eff = show["Efficiency"]
    st.dataframe(disp.style.apply(lambda col: [cell_color(v) if pd.notna(v) and 0 < v < 5 else "" for v in eff], subset=["Efficiency"]), width="stretch")
    st.download_button("Download table (CSV)", show.to_csv().encode(), "sample_creation_overview.csv", "text/csv", key="sm_dl_ov")
    st.markdown("**Standard minutes for sample sizes**")
    st.dataframe(sm.standard_samples(S).style.format({"Standard minutes": "{:.1f}"}), width="stretch", hide_index=True)
    fit = sm.fit_standard(samples)
    if fit:
        st.caption(f"Best fit on the rated samples in this period: a = {fit[0]:,.0f} s, b = {fit[1]:.1f} s/item, R² = {fit[2]:.2f}. "
                   f"Current settings: a = {S.a:,.0f}, b = {S.b:.1f}. R² is low because item count explains only part of the time - treat percentages as a guide, "
                   "especially for packers with few samples.")
    else:
        st.caption("Too few rated samples in this period to show a best fit.")


def period_tab(period, fmt):
    m = sm.period_matrices(view, S, period)
    if m["efficiency"].empty:
        st.info("No rated samples in this period."); return
    e, p = m["efficiency"].copy(), m["samples_rated"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated samples")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    st.line_chart(chart.drop(columns=[c for c in ["All packers"] if c in chart.columns]))
    st.markdown("**Samples rated** (the work behind each percentage)")
    st.dataframe(p.style.format("{:,.0f}", na_rep="-"), width="stretch")
    st.markdown("**Average minutes per sample**")
    mn = m["min_per_sample"].copy(); mn.columns = e.columns
    st.dataframe(mn.style.format("{:.1f}", na_rep="-"), width="stretch")


with tab_w:
    st.subheader("Weekly (Monday to Sunday, by date created)")
    period_tab("week", "%d-%b")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y")

# --------------------------------------------------------------- review flags
with tab_f:
    st.subheader("Where samples were not rated")
    st.dataframe(sm.status_counts(samples), width="stretch")
    st.markdown("- **Overnight**: shipped on a different day than created - expected, not rated.\n"
                "- **End before start / missing timestamp**: a timestamp problem worth a look.\n"
                "- **No packer / not in team list / status**: no one to rate, or cancelled / still active.")
    ft = sm.flags_table(view)
    kinds = sorted(ft["status_text"].unique())
    pick = st.multiselect("Show", kinds, default=[k for k in kinds if "overnight" not in k], key="sm_flag_kinds")
    ft = ft[ft["status_text"].isin(pick)] if pick else ft.iloc[0:0]
    st.dataframe(ft.rename(columns={"name": "Packer", "created_at": "Created", "shipping_time": "Shipped", "dur": "Seconds",
                                    "total_items": "Items", "std_sec": "Standard seconds", "status_text": "Status"})
                 .style.format({"Created": lambda v: v.strftime("%d-%b-%y %H:%M"), "Shipped": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M"),
                                "Seconds": "{:,.0f}", "Standard seconds": "{:.0f}", "Items": "{:,.0f}"}, na_rep="-"),
                 width="stretch", height=400)
    st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "sample_creation_review_flags.csv", "text/csv", key="sm_dl_flags")

# ------------------------------------------------------------------ how it works
with tab_how:
    st.markdown(f"""
### What this answers
How long does each packer take to create a sample, compared with the time a sample of that many items should take? 100% = at the standard, above = faster, below = slower.

### The process
Slips are printed and handed to the packer, who collects the medicines, blacks out everything except composition and expiry on **each item**, and packs them in a box.
The packer does the whole process (the assembler often only clicks a button), so the work is rated on the **packer**.

### Time and size
- **Time** = `shipping_time` - `created_at` (the whole documented process, including the final verification stage). Label time is not used.
- **Size** = number of **items** (SKU count is ignored - every item is blacked out by hand).

### The standard
Standard seconds = **{S.a:g} + {S.b:g} x items**: about {S.a / 60:.0f} minutes fixed plus {S.b:g} seconds per item. Fitted on {'same-day' if S.exclude_overnight else 'all'} past samples so the team sits near 100%.

### Rated samples
Status `past`, packer on the team list, both timestamps present, shipped after created, shipped the same day (processes cannot run overnight), items > 0. All other rows are counted on Review flags.

### How efficiency is calculated
Add the standard seconds and the actual seconds of a packer's rated samples, then divide once (not an average of per-sample percentages).

### Caution
Item count explains only part of the variation, and samples are few. Use the percentages as a guide; the job count is shown beside each one.
""")
    with st.expander("Data checks"):
        chk = sm.data_checks(samples)
        st.table(pd.DataFrame({"Check": list(chk.keys()), "Value": [f"{v:,}" if isinstance(v, int) else v for v in chk.values()]}))
