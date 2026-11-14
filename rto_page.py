"""RTO (returned parcels) efficiency page (runs inside app.py's navigation).

One process, shown as one result. Behind the scenes it is two phases: Phase 1 carton sorting (per parcel) and
Phase 2 trolley transfer + merge to base (per carton).
"""
import os
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

import config
import db
import rto
from rto import RtoSettings
from ui_common import cell_color, style_eff, settings_area, is_admin, data_checks_box

# ------------------------------------------------------------------ top bar
top_l, top_s, top_r = st.columns([6, 1.4, 1.4])
top_l.title("RTO efficiency vs the standard")
settings_pop = settings_area(top_s)
if top_r.button("Refresh data", use_container_width=True, key="rt_refresh"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------------------------ settings
S = RtoSettings()
with settings_pop:
    st.markdown("**Settings** - every number on this page recalculates from these")
    st.markdown("**Phase 1 - carton sorting (per parcel)**")
    S.p1_std = st.number_input("Standard seconds per parcel", value=float(config.RTO_P1_STD_SEC), step=0.5, format="%.1f", key="rt_p1",
                               help="Gap since the same person's previous carton_added_at. Default = mean of rated gaps, so the team sits near 100%.")
    S.p1_cap = st.number_input("Longest gap rated (sec)", value=int(config.RTO_P1_CAP_SEC), step=30, min_value=1, key="rt_p1cap",
                               help="A longer gap is a break or a stop and is not rated. 300 s keeps about 97% of gaps.")
    st.markdown("**Phase 2 - trolley transfer + merge (carton by carton)**")
    S.t_fixed = st.number_input("Transfer: fixed seconds per carton", value=float(config.RTO_T_FIXED), step=10.0, format="%.2f", key="rt_tf")
    S.t_parcel = st.number_input("Transfer: seconds per parcel", value=float(config.RTO_T_PARCEL), step=1.0, format="%.2f", key="rt_tp",
                                 help="Open, inspect, check damage / missing, submit, place on the trolley.")
    S.t_unit = st.number_input("Transfer: seconds per unit", value=float(config.RTO_T_UNIT), step=0.1, format="%.2f", key="rt_tu")
    S.m_fixed = st.number_input("Merge: fixed seconds per carton", value=float(config.RTO_M_FIXED), step=10.0, format="%.2f", key="rt_mf")
    S.m_drug = st.number_input("Merge: seconds per drug code", value=float(config.RTO_M_DRUG), step=0.5, format="%.2f", key="rt_md",
                               help="Walk to the cubicle / rack, place the medicine, enter the quantity, Merge.")
    S.m_unit = st.number_input("Merge: seconds per unit", value=float(config.RTO_M_UNIT), step=0.1, format="%.2f", key="rt_mu")
    S.ratio = st.number_input("Max actual / standard for one carton", value=float(config.RTO_MAX_RATIO), step=0.5, min_value=1.0, key="rt_ratio",
                              help="A carton that took more than this many times its own standard (its standard comes from its size) is treated as a break or an operational problem and is not rated. Raise it if fewer than 95% of jobs are rated.")
    S.hard_cap = st.number_input("Hard cap on a carton (sec)", value=int(config.RTO_HARD_CAP_SEC), step=600, min_value=60, key="rt_hard",
                                 help="No carton longer than this is rated, whatever its size. 14400 = 4 hours.")
    st.markdown("**Combined result**")
    S.combined_from = st.date_input("Count Phase 1 in the combined result from", value=dt.date.fromisoformat(config.RTO_COMBINED_FROM), key="rt_from",
                                    help="Phase 2 data only exists from early September 2026 (the audit tables are new), so Phase 1 is counted from the same day to keep both phases on the same days.")
    S.min_show = st.number_input("Min rated parcels + cartons to show a person's efficiency", value=30, step=5, min_value=0, key="rt_show")
    S.min_show_period = st.number_input("Min rated parcels + cartons to show a month cell", value=15, step=5, min_value=0, key="rt_showp")
    S.min_show_day = st.number_input("Min rated parcels + cartons to show a day / week cell", value=10, step=1, min_value=0, key="rt_showd")
    S.min_jobs = st.number_input("Min rated cartons to show a Phase 2 efficiency", value=8, step=1, min_value=0, key="rt_minj")
    S.low_sample = st.number_input("Low-sample marker: parcels + cartons below", value=10, step=1, min_value=0, key="rt_low",
                                   help="Cells built on fewer rated parcels + cartons than this appear in grey italics.")
    st.caption("Person = carton_added_by (Phase 1) and the person on the trolley session (Phase 2). Anurag only scans parcels in and is not rated (config.py).")

# ------------------------------------------------------------ data source + date window
today = dt.datetime.now(db.IST).date()
dev_sort, dev_main, dev_items = os.environ.get("DEV_RTO_SORT_CSV"), os.environ.get("DEV_RTO_AUDIT_CSV"), os.environ.get("DEV_RTO_ITEMS_CSV")
live = db.secrets_available() and not dev_sort
if not live and not dev_sort:
    st.info("RTO reads the live database. Add the database secrets (see README) to use this page.")
    st.stop()
file_sort = None
if dev_sort:
    file_sort = pd.read_csv(dev_sort)[["order_id", "carton_added_by", "carton_added_at"]]
    file_sort["carton_added_at"] = pd.to_datetime(file_sort["carton_added_at"], errors="coerce")
    file_sort = file_sort[file_sort["carton_added_at"].notna()]

chosen_window = None
with st.container(border=True):
    presets = (["Last 7 days", "Last 30 days", "Last 90 days", "This month", "Last month", "Custom"] if live else ["All data in file", "Custom"])
    p1, p2, p3, p4 = st.columns([2, 2, 2, 1])
    preset = p1.selectbox("Period (by date sorted / carton started)", presets, index=None, placeholder="Select a period", key="rt_preset")
    d_from = d_to = None
    if preset == "Custom":
        if live:
            lo, hi, v_from, v_to = None, None, today - dt.timedelta(days=29), today
        else:
            lo, hi = file_sort["carton_added_at"].min().date(), file_sort["carton_added_at"].max().date(); v_from, v_to = lo, hi
        d_from = p2.date_input("From", value=v_from, min_value=lo, max_value=hi, key="rt_dfrom")
        d_to = p3.date_input("To", value=v_to, min_value=lo, max_value=hi, key="rt_dto")
    load_clicked = p4.button("Load", type="primary", disabled=preset is None, key="rt_load")
    if preset == "Custom":
        chosen_window = (d_from, d_to)
    elif preset == "All data in file":
        chosen_window = (file_sort["carton_added_at"].min().date(), file_sort["carton_added_at"].max().date())
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
    st.session_state["rt_loaded"] = {"start": chosen_window[0], "end": chosen_window[1]}
loaded = st.session_state.get("rt_loaded")
if not loaded:
    st.info("Choose a period above and press **Load** to read the data. Carton-sorting stamps exist since 19 Aug 2026; the trolley / merge audit data since early September 2026.")
    st.stop()
start, end = loaded["start"], loaded["end"]
if chosen_window and chosen_window != (start, end):
    st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}. You picked a different period - press **Load** to apply it.")

s_dt, e_dt, s_ms, e_ms = db.window(start, end, pad_seconds=0)
audit_note = ""
try:
    if live:
        raw_sort = db.fetch_rto_sort(s_ms, e_ms)
    else:
        raw_sort = file_sort[(file_sort["carton_added_at"] >= s_dt) & (file_sort["carton_added_at"] < e_dt)]
except Exception as e:  # readable message, never the secrets
    st.error(f"Could not read the database: {type(e).__name__}. Check the secrets and the SSH/DB access.")
    st.stop()
try:
    if live:
        a_main, a_items = db.fetch_rto_audit()
    elif dev_main:
        a_main, a_items = rto.normalize_audit(pd.read_csv(dev_main), pd.read_csv(dev_items) if dev_items else None)
    else:
        a_main = a_items = None
except Exception as e:
    a_main = a_items = None
    audit_note = f"The trolley / merge audit table could not be read ({type(e).__name__}: {str(e)[:160]}). Phase 2 is left out, so the result below is Phase 1 only."

sort_d = rto.rate_sort(raw_sort, S)
if a_main is not None and len(a_main):
    jobs_all = rto.rate_jobs(rto.pair_jobs(a_main, a_items), S)
    jobs_d = jobs_all[(jobs_all["start"] >= s_dt) & (jobs_all["start"] < e_dt)]
else:
    jobs_d = rto.rate_jobs(rto.pair_jobs(pd.DataFrame({c: [] for c in ["id", "workflow_step", "performed_by", "carton_number", "item_count", "started_at", "ended_at", "end_reason"]}),
                                         pd.DataFrame(columns=["audit_id", "drug_code", "quantity"])), S)
if audit_note:
    st.warning(audit_note)
elif len(jobs_d) and jobs_d["dc"].notna().sum() == 0:
    st.warning("Cartons were found but none has merge items (drug codes), so none could be rated. Check the item_details column of the audit table.")
if sort_d.empty and jobs_d.empty:
    st.info(f"No RTO work between {start:%d %b %Y} and {end:%d %b %Y}."); st.stop()

u_all = rto.units_table(sort_d, jobs_d, S)
names = rto.name_order(u_all["name"].unique())
st.caption(f"Showing {start:%d %b %Y} to {end:%d %b %Y}.")
if not names:
    st.warning("Nothing in this period could be rated in the combined result. See Review flags for the reasons.")
chosen = st.multiselect("People", names, default=names, key="rt_chosen")
u = u_all[u_all["name"].isin(chosen)] if chosen else u_all.iloc[0:0]
sort_v = sort_d[sort_d["name"].isin(chosen) | ~sort_d["rated"]] if chosen and len(sort_d) else sort_d
jobs_v = jobs_d[jobs_d["name"].isin(chosen) | ~jobs_d["rated"]] if chosen and len(jobs_d) else jobs_d

# ---------------------------------------------------------------------- header
ov = rto.combined_table(u, S) if len(u) else pd.DataFrame()
tot = ov.loc["Team"] if "Team" in ov.index else None
c1, c2, c3, _ = st.columns(4)
c1.metric("Efficiency (all selected)", "-" if tot is None or pd.isna(tot["efficiency"]) else f"{tot['efficiency']:.1%}",
          help="(Phase 1 + Phase 2 standard seconds) / (Phase 1 + Phase 2 actual seconds) over rated work. Standards are fitted on the team, so the team sits near 100% by design.")
c2.metric("Parcels sorted (rated)", "-" if tot is None else f"{int(tot['parcels']):,}")
c3.metric("Cartons processed (rated)", "-" if tot is None else f"{int(tot['jobs']):,}",
          help="Whole cartons taken through trolley transfer + merge (Phase 2). One carton holds about 17 parcels, so this is a much smaller number than parcels sorted.")
st.caption(f"100% = at the team standard; above 100% = faster. Counted from {S.combined_from:%d %b %Y} (the first day with Phase 2 data). Anurag only scans parcels in and is not rated.")

tab_ov, tab_d, tab_w, tab_m, tab_f, tab_how = st.tabs(["Overview", "Daily", "Weekly", "Monthly", "Review flags", "How it works"])

with tab_ov:
    st.subheader("Each person against the standard (Phase 1 + Phase 2)")
    if ov.empty:
        st.info("No rated work in this period (the combined result starts on the date in Settings).")
    else:
        show = pd.DataFrame({"Parcels sorted": ov["parcels"].astype(int), "Cartons processed": ov["jobs"].astype(int), "Efficiency": ov["efficiency"]})
        disp = show.copy()
        disp["Efficiency"] = show["Efficiency"].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")
        eff = show["Efficiency"]
        st.dataframe(disp.style.apply(lambda col: [cell_color(v) if pd.notna(v) and 0 < v < 5 else "" for v in eff], subset=["Efficiency"]), width="stretch")
        st.caption(f"A dash = fewer than {S.min_show} rated parcels + cartons. One carton holds about 17 parcels.")
        st.download_button("Download table (CSV)", show.to_csv().encode(), "rto_overview.csv", "text/csv", key="rt_dl_ov")

    st.subheader("Phase 1 - carton sorting")
    p1t = rto.phase1_table(sort_v, S) if len(sort_v) and sort_v["rated"].any() else pd.DataFrame()
    if p1t.empty:
        st.info("No rated parcels in this period.")
    else:
        d1 = pd.DataFrame({"Rated parcels": p1t["parcels"].astype(int), "Efficiency": p1t["efficiency"].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")})
        st.dataframe(d1, width="stretch")

    st.subheader("Phase 2 - trolley transfer + merge to base (carton by carton)")
    p2t = rto.phase2_table(jobs_v, S) if len(jobs_v) and jobs_v["rated"].any() else pd.DataFrame()
    if p2t.empty:
        st.info("No rated cartons in this period.")
    else:
        d2 = pd.DataFrame({"Cartons rated": p2t["jobs"].astype(int), "Efficiency": p2t["eff_all"].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")})
        st.dataframe(d2, width="stretch")
        st.caption(f"A dash = fewer than {S.min_jobs} rated cartons.")
    if is_admin():
        sh = rto.rated_shares(sort_d, jobs_d); sg = rto.suggested(sort_d, jobs_d)
        bits = [f"Rated share: Phase 1 {sh['p1']:.1%}, Phase 2 {sh['p2']:.1%} (aim for 95% or more; raise the cap / ratio in Settings if lower)."]
        if "t" in sg and "m" in sg:
            bits.append("This period would fit: transfer {:.0f} + {:.1f} x parcels + {:.1f} x units; merge {:.0f} + {:.1f} x drug codes + {:.1f} x units. Current settings: transfer {:.0f} + {:.1f} + {:.1f}; merge {:.0f} + {:.1f} + {:.1f}.".format(
                *sg["t"], *sg["m"], S.t_fixed, S.t_parcel, S.t_unit, S.m_fixed, S.m_drug, S.m_unit))
        st.caption(" ".join(bits))


def period_tab(period, fmt, min_n, key):
    if u.empty:
        st.info("No rated work in this period."); return
    m = rto.period_matrices(u, [n for n in names if n in chosen], period, min_n)
    e, p = m["efficiency"].copy(), m["n"]
    e.columns = [c.strftime(fmt) if hasattr(c, "strftime") else c for c in e.columns]
    p = p.copy(); p.columns = e.columns
    st.markdown(f"**Efficiency vs standard** - grey italics = fewer than {S.low_sample} rated parcels + cartons")
    st.dataframe(style_eff(e, p, S.low_sample), width="stretch")
    chart = e.drop(columns="Overall").T
    st.line_chart(chart.drop(columns=[c for c in ["Team"] if c in chart.columns]))


with tab_d:
    st.subheader("Daily")
    period_tab("date", "%d-%b", S.min_show_day, "d")
with tab_w:
    st.subheader("Weekly (Monday to Sunday)")
    period_tab("week", "%d-%b", S.min_show_day, "w")
with tab_m:
    st.subheader("Monthly")
    period_tab("month", "%b-%y", S.min_show_period, "m")

with tab_f:
    st.subheader("Where work was not rated")
    st.dataframe(rto.status_counts(sort_d, jobs_d), width="stretch")
    st.caption("Rated = counted in the efficiency. Every other row is a reason why a parcel or carton was left out (it is not deleted, it just does not count). The 95% rule is checked only on work that could be rated (not on 'not on team list' or 'no login').")
    st.markdown("- **Phase 1 too long**: a gap over the cap - a break or a stop, not slow sorting.\n"
                "- **Phase 2 too long for its size / hard cap**: the carton took far longer than its own standard (its standard comes from its size) - a break, an interruption or an operational problem.\n"
                "- **No merge session**: the trolley session has no auto_drained merge session after it (reset or superseded).\n"
                "- **Not on team list**: Anurag (scan-in only), admin and other logins in `RTO_EXCLUDED_LOGINS`.")
    ft = rto.flags_table(sort_d, jobs_d)
    if ft.empty:
        st.info("No cartons were left unrated for size, time or a missing merge session.")
    else:
        with st.expander("Show the individual cartons"):
            st.dataframe(ft.rename(columns={"name": "Person", "carton": "Carton", "start": "Carton start", "end": "Carton end", "act": "Seconds", "std_total": "Standard sec",
                                            "parcels": "Parcels", "dc": "Drug codes", "units": "Units", "status_text": "Status"})
                         .style.format({"Carton start": lambda v: v.strftime("%d-%b-%y %H:%M"), "Carton end": lambda v: "-" if pd.isna(v) else v.strftime("%d-%b-%y %H:%M"),
                                        "Seconds": "{:,.0f}", "Standard sec": "{:,.0f}", "Carton": "{:.0f}", "Drug codes": "{:.0f}", "Units": "{:,.0f}"}, na_rep="-"), width="stretch", height=300)
            st.download_button("Download flags (CSV)", ft.to_csv(index=False).encode(), "rto_review_flags.csv", "text/csv", key="rt_dl_flags")

with tab_how:
    st.markdown(f"""
### What this answers
How fast does each person work through the RTO process, compared with the team standard? 100% = at the standard, above = faster, below = slower.

### The process
Returned parcels are scanned in (by Anurag - not measured) and sit. When an executive is free he starts the RTO: the system sorts the parcels into up to 5 cartons.
Then, carton by carton, he scans each parcel, opens it, checks the medicines for damage or missing units, submits it (the system says where on the 48-slot trolley the drug goes),
takes the full trolley to the cubicles and merges each drug to base. A carton holds at most 48 drug codes.

### One process, two phases
It is one process, split in two for the calculation and shown here as one result:
**Phase 1** - carton sorting, one stamp per parcel (`carton_added_at`, by `carton_added_by`).
**Phase 2** - trolley transfer + merge to base, carton by carton (the audit tables, `workflow` trolley_transfer and merge). A *carton* is one whole carton taken through trolley transfer and then merge; a person normally does 1 to 5 cartons a day, and one carton holds about 17 parcels.

### Phase 1 time and standard
Duration of a parcel = its `carton_added_at` minus the same person's previous one. A gap over {S.p1_cap:g} seconds is a break and is not rated. Standard = **{S.p1_std:g} seconds** per parcel.

### Phase 2 time and standard
**One carton's time.** The clock starts at the first parcel scan of the carton in Trolley Transfer and stops at `ended_at` of the Merge session that follows for the same person and carton (only sessions ending `auto_drained`; reset and superseded sessions were abandoned and hold no items).

**Why two parts?** Inside a carton the person does two kinds of work: first *transfer* (scan, open, check each parcel, place the medicines on the trolley), then *merge* (take the trolley to the cubicles and put every medicine back to base). The system does not stamp when the transfer ends, but it does stamp when the Merge session *starts*. That moment is the dividing line: before it = transfer part, after it = merge part. Each part has its own standard.

**Standard** = transfer (**{S.t_fixed:g} + {S.t_parcel:g} x parcels + {S.t_unit:g} x units**) + merge (**{S.m_fixed:g} + {S.m_drug:g} x drug codes + {S.m_unit:g} x units**), in seconds. Units = quantity merged back; drug codes = distinct drug codes merged. The numbers were fitted on the team's own cartons, which is why the team sits near 100%; the small negative fixed term is a side effect of the fit, not a real negative time.

**Cartons we leave out, and why.** A carton is left out only when it took far longer than it should for its size, because then the time usually shows a break, a phone call, waiting or a system problem, not speed: (1) more than **{S.ratio:g} times** its own standard (a carton with a 30-minute standard is left out above {30*S.ratio:g} minutes), (2) more than {S.hard_cap:g} seconds whatever its size, (3) bad data (no finished merge session, or merge ends before the carton starts). Left-out cartons are not deleted: see the Review flags tab. The ratio is chosen so at least 95% of the cartons that could be rated are counted.

### How efficiency is calculated
Add the standard seconds and the actual seconds of a person's rated parcels (Phase 1) and rated cartons (Phase 2), then divide once. What is added is always seconds, never counts, so parcels and cartons can sit in the same sum. The combined result counts Phase 1 only from {S.combined_from:%d %b %Y}, the first day with Phase 2 data.
People with fewer than {S.min_show} rated parcels + cartons show no percentage (a % built on very little work is mostly luck).

### Caution
The audit tables are new, so Phase 2 has few cartons and most are one person's. Use the percentages as a guide. The standards are fitted on this data, so the team sits near 100% by design.

### Work not separated yet (to confirm with the RTO team)
The standard is built from time stamps, so anything not stamped is mixed into the actual time. Still to confirm with the people who do RTO: parcels that are not SayaCare parcels (photo or video needed?), damaged or leaking parcels, missing or wrong medicines, unreadable barcodes, medicines that are not merged back, wrong-carton re-sorting, system freezes, waiting for trolley / cubicle / stock, and other duties in the middle of a carton. If a case is frequent and slow it should get its own stamp or log and standard; if rare, the ratio rule already leaves out the very long ones.
""")
    if is_admin():
        data_checks_box(rto.data_checks(sort_d, jobs_d))
