"""RTO (returned parcels) efficiency logic. Pure pandas - no Streamlit, no database.

One process, two phases (same rules as RTO_Efficiency_v3.xlsx):
 * Phase 1 - carton sorting, one stamp per parcel (return_shipment_tracking.carton_added_at / carton_added_by).
   duration = gap since the same person's previous stamp; standard = fixed seconds per parcel.
 * Phase 2 - trolley transfer + merge to base, one job per carton (return_shipment_tracking_audit).
   job = Trolley Transfer session start -> ended_at of the Merge session that follows for the same person and carton
   (end_reason 'auto_drained' only). The merge start splits it into a transfer part and a merge part.
   standard = transfer (fixed + parcel x parcels + unit x units) + merge (fixed + drug code x drug codes + unit x units).
 * Combined efficiency = (Phase 1 standard + Phase 2 standard) / (Phase 1 actual + Phase 2 actual) over rated work.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import datetime as dt
import json
import numpy as np
import pandas as pd

import config
from efficiency import exec_name, LOGIN_NAMES

NOT_RATED_P1 = ["Not rated: too long", "Not rated: zero gap"]
NOT_RATED_P2 = ["Not rated: too long for its size", "Not rated: too long (hard cap)", "Not rated: no merge session", "Not rated: end before start"]


@dataclass
class RtoSettings:
    p1_std: float = config.RTO_P1_STD_SEC
    p1_cap: float = config.RTO_P1_CAP_SEC
    t_fixed: float = config.RTO_T_FIXED
    t_parcel: float = config.RTO_T_PARCEL
    t_unit: float = config.RTO_T_UNIT
    m_fixed: float = config.RTO_M_FIXED
    m_drug: float = config.RTO_M_DRUG
    m_unit: float = config.RTO_M_UNIT
    ratio: float = config.RTO_MAX_RATIO
    hard_cap: float = config.RTO_HARD_CAP_SEC
    combined_from: dt.date = field(default_factory=lambda: dt.date.fromisoformat(config.RTO_COMBINED_FROM))
    excluded: frozenset = frozenset(config.RTO_EXCLUDED_LOGINS)
    min_show: int = 30           # per person, overall
    min_show_period: int = 15    # per person per month
    min_show_day: int = 10       # per person per day / week
    min_jobs: int = 8            # Phase 2 table
    low_sample: int = 10


# ------------------------------------------------------------------ reading the audit table (live)
def to_ist(s: pd.Series) -> pd.Series:
    """Epoch (ms or s), text or datetime -> naive IST datetime."""
    s = s.replace({"NA": np.nan, "": np.nan}) if s.dtype == object else s
    if pd.api.types.is_datetime64_any_dtype(s):
        if getattr(s.dt, "tz", None) is not None:
            return s.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return s
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().sum() >= max(1, int(0.5 * s.notna().sum())) and s.notna().any():
        sec = np.where(num > 1e11, num / 1000.0, num)
        return pd.to_datetime(pd.Series(sec, index=s.index), unit="s", errors="coerce") + pd.Timedelta(hours=5, minutes=30)
    out = pd.to_datetime(s, errors="coerce", utc=True)
    return out.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)


_DRUG_KEYS = ("drug_code", "drugCode", "drug", "code", "Drug Code")
_QTY_KEYS = ("quantity", "Quantity", "qty", "merged_quantity", "merged_qty")


def _pick(d: dict, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def parse_item_details(v) -> list[tuple]:
    """item_details (JSON) -> [(drug_code, quantity), ...]. Accepts a list of objects or an object keyed by drug code."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "ignore")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return []
    out = []
    if isinstance(v, dict):
        if all(isinstance(x, dict) for x in v.values()) and v and not any(k in v for k in _DRUG_KEYS):
            for k, x in v.items():
                dc = _pick(x, _DRUG_KEYS)
                out.append((k if dc is None else dc, _pick(x, _QTY_KEYS)))
        else:
            out.append((_pick(v, _DRUG_KEYS), _pick(v, _QTY_KEYS)))
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                out.append((_pick(x, _DRUG_KEYS), _pick(x, _QTY_KEYS)))
    return [(str(a), pd.to_numeric(b, errors="coerce")) for a, b in out if a is not None]


def normalize_audit(main: pd.DataFrame, items: pd.DataFrame | None = None):
    """Audit rows -> (sessions, items). `items` (audit_id, drug_code, quantity) is built from item_details when not given."""
    m = main.copy()
    for cand in ("workflow_step", "workflow", "step"):
        if cand in m.columns:
            if cand != "workflow_step":
                m = m.rename(columns={cand: "workflow_step"})
            break
    need = ["id", "workflow_step", "performed_by", "carton_number", "item_count", "started_at", "ended_at", "end_reason"]
    miss = [c for c in need if c not in m.columns]
    if miss:
        raise ValueError("the audit table is missing the columns: " + ", ".join(miss))
    for c in ("started_at", "last_activity_at", "ended_at"):
        if c in m.columns:
            m[c] = to_ist(m[c])
    m["item_count"] = pd.to_numeric(m["item_count"], errors="coerce").fillna(0).astype(int)
    m["carton_number"] = pd.to_numeric(m["carton_number"], errors="coerce")
    if items is None:
        rows = []
        if "item_details" in m.columns:
            for _, r in m[m["workflow_step"] == "merge"].iterrows():
                for dc, q in parse_item_details(r["item_details"]):
                    rows.append((r["id"], dc, q))
        items = pd.DataFrame(rows, columns=["audit_id", "drug_code", "quantity"])
        m = m.drop(columns=[c for c in ("item_details",) if c in m.columns])
    else:
        items = items[["audit_id", "drug_code", "quantity"]].copy()
        items["quantity"] = pd.to_numeric(items["quantity"], errors="coerce")
    return m, items


# ------------------------------------------------------------------ Phase 1
def _add_periods(d: pd.DataFrame, col: str) -> pd.DataFrame:
    d["date"] = d[col].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    return d


def rate_sort(df: pd.DataFrame, s: RtoSettings) -> pd.DataFrame:
    """df: order_id, carton_added_by, carton_added_at (one row per parcel stamp)."""
    d = df.copy()
    if d.empty:
        d = pd.DataFrame({"order_id": [], "carton_added_by": [], "carton_added_at": pd.Series([], dtype="datetime64[ns]")})
    d["ts"] = pd.to_datetime(d["carton_added_at"], errors="coerce")
    d = d[d["ts"].notna()].copy()
    d["login"] = d["carton_added_by"].fillna("").astype(str).str.strip()
    d = d.sort_values(["login", "ts"], kind="mergesort").reset_index(drop=True)
    same = d["login"] == d["login"].shift()
    d["gap"] = np.where(same, d["ts"].diff().dt.total_seconds().round(), np.nan)
    d["status_text"] = np.select(
        [d["login"] == "", d["login"].isin(s.excluded), d["gap"].isna(), d["gap"] <= 0, d["gap"] > s.p1_cap],
        ["Not rated: no login", "Not rated: not on team list", "Not rated: first of run", "Not rated: zero gap", "Not rated: too long"],
        default="Rated")
    d["rated"] = d["status_text"] == "Rated"
    d["name"] = d["login"].map(lambda x: exec_name(x) if x else "(no login)")
    d["std"] = s.p1_std
    return _add_periods(d, "ts")


# ------------------------------------------------------------------ Phase 2
def pair_jobs(main: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    """One row per auto_drained Trolley Transfer session, joined to the Merge session that follows it (same person and carton)."""
    trall = main[main["workflow_step"] == "trolley_transfer"].sort_values("started_at")
    tr = trall[trall["end_reason"] == "auto_drained"]
    mg = main[(main["workflow_step"] == "merge") & (main["end_reason"] == "auto_drained")]
    agg = items.groupby("audit_id").agg(dc=("drug_code", "nunique"), units=("quantity", "sum")) if len(items) else pd.DataFrame(columns=["dc", "units"])
    rows = []
    for _, a in tr.iterrows():
        nxt = trall[(trall["performed_by"] == a["performed_by"]) & (trall["started_at"] > a["started_at"]) & (trall["item_count"] > 0)]
        lim = nxt["started_at"].min() if len(nxt) else pd.Timestamp.max
        c = mg[(mg["performed_by"] == a["performed_by"]) & (mg["carton_number"] == a["carton_number"])
               & (mg["started_at"] >= a["started_at"] - pd.Timedelta("60s")) & (mg["started_at"] < lim)].sort_values("started_at")
        base = dict(tid=a["id"], login=a["performed_by"], carton=a["carton_number"], start=a["started_at"], parcels=int(a["item_count"]))
        if c.empty:
            rows.append({**base, "mid": np.nan, "hand": pd.NaT, "end": pd.NaT, "dc": np.nan, "units": np.nan}); continue
        b = c.iloc[0]
        if b["id"] in agg.index:
            dc, un = float(agg.loc[b["id"], "dc"]), float(agg.loc[b["id"], "units"])
        else:
            dc = un = np.nan
        rows.append({**base, "mid": b["id"], "hand": b["started_at"], "end": b["ended_at"], "dc": dc, "units": un})
    cols = ["tid", "mid", "login", "carton", "start", "hand", "end", "parcels", "dc", "units"]
    return pd.DataFrame(rows, columns=cols)


def rate_jobs(j: pd.DataFrame, s: RtoSettings) -> pd.DataFrame:
    d = j.copy()
    for c in ("start", "hand", "end"):
        d[c] = pd.to_datetime(d[c], errors="coerce")
    for c in ("parcels", "dc", "units"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["login"] = d["login"].fillna("").astype(str)
    d["name"] = d["login"].map(lambda x: exec_name(x) if x else "(no login)")
    d["act"] = (d["end"] - d["start"]).dt.total_seconds().round()
    hand = (d["hand"] - d["start"]).dt.total_seconds().round()
    d["tsec"] = np.maximum(0, np.minimum(d["act"], hand))
    d["msec"] = d["act"] - d["tsec"]
    u = d["units"].fillna(0)
    d["std_t"] = s.t_fixed + s.t_parcel * d["parcels"] + s.t_unit * u
    d["std_m"] = s.m_fixed + s.m_drug * d["dc"].fillna(0) + s.m_unit * u
    d["std_total"] = d["std_t"] + d["std_m"]
    d["ratio"] = np.where(d["act"] > 0, d["act"] / d["std_total"], np.nan)
    d["status_text"] = np.select(
        [d["login"] == "", d["login"].isin(s.excluded), d["end"].isna() | d["dc"].isna(), d["act"] <= 0, d["act"] > s.hard_cap, d["ratio"] > s.ratio],
        ["Not rated: no login", "Not rated: not on team list", "Not rated: no merge session", "Not rated: end before start",
         "Not rated: too long (hard cap)", "Not rated: too long for its size"], default="Rated")
    d["rated"] = d["status_text"] == "Rated"
    return _add_periods(d, "start")


# ------------------------------------------------------------------ combined
def units_table(sort_d: pd.DataFrame, jobs_d: pd.DataFrame, s: RtoSettings) -> pd.DataFrame:
    """Rated work of both phases in one table: actual and standard seconds per parcel (Phase 1) or per job (Phase 2)."""
    a = sort_d[sort_d["rated"] & (sort_d["date"] >= pd.Timestamp(s.combined_from))]
    b = jobs_d[jobs_d["rated"]]
    ua = pd.DataFrame({"name": a["name"], "date": a["date"], "week": a["week"], "month": a["month"], "act": a["gap"], "std": s.p1_std, "parcels": 1, "jobs": 0})
    ub = pd.DataFrame({"name": b["name"], "date": b["date"], "week": b["week"], "month": b["month"], "act": b["act"], "std": b["std_total"], "parcels": 0, "jobs": 1})
    return pd.concat([ua, ub], ignore_index=True)


def name_order(names) -> list[str]:
    present = set(names)
    ordered = [n for n in LOGIN_NAMES.values() if n in present]
    seen, out = set(), []
    for n in ordered:
        if n not in seen:
            out.append(n); seen.add(n)
    return out + sorted(present - seen)


def summarize(u: pd.DataFrame, keys, min_n: int) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    g = u.assign(_all=1).groupby(keys if keys else ["_all"])
    t = g.agg(parcels=("parcels", "sum"), jobs=("jobs", "sum"), act=("act", "sum"), std=("std", "sum"))
    t["n"] = t["parcels"] + t["jobs"]
    ok = (t["act"] > 0) & (t["n"] > 0)
    t["efficiency"] = np.where(ok & (t["n"] >= min_n), t["std"] / t["act"].where(ok, 1), np.nan)
    t["hours"] = t["act"] / 3600
    if not keys:
        t = t.reset_index(drop=True)
    return t


def combined_table(u: pd.DataFrame, s: RtoSettings) -> pd.DataFrame:
    names = name_order(u["name"].unique())
    t = summarize(u, "name", s.min_show).reindex(names)
    tot = summarize(u, [], 0); tot.index = ["Team"]
    t = pd.concat([t, tot])
    t["verdict"] = np.where(t["efficiency"].isna(), "-", np.where(t["efficiency"] >= 1, "Above team standard", "Below team standard"))
    t.loc["Team", "verdict"] = "-"
    return t


def phase1_table(sort_d: pd.DataFrame, s: RtoSettings) -> pd.DataFrame:
    r = sort_d[sort_d["rated"]]
    names = name_order(r["name"].unique())
    g = r.groupby("name").agg(parcels=("gap", "size"), act=("gap", "sum"), median_sec=("gap", "median"), mean_sec=("gap", "mean"))
    g["std"] = g["parcels"] * s.p1_std
    g["efficiency"] = np.where((g["act"] > 0) & (g["parcels"] >= s.min_show), g["std"] / g["act"].where(g["act"] > 0, 1), np.nan)
    g["too_long"] = sort_d[sort_d["status_text"] == "Not rated: too long"].groupby("name").size().reindex(g.index).fillna(0).astype(int)
    g = g.reindex(names)
    tot = pd.DataFrame({"parcels": [len(r)], "act": [r["gap"].sum()], "median_sec": [r["gap"].median() if len(r) else np.nan], "mean_sec": [r["gap"].mean() if len(r) else np.nan],
                        "std": [len(r) * s.p1_std], "efficiency": [(len(r) * s.p1_std) / r["gap"].sum() if len(r) and r["gap"].sum() > 0 else np.nan],
                        "too_long": [int((sort_d["status_text"] == "Not rated: too long").sum())]}, index=["Team"])
    return pd.concat([g, tot])


def phase2_table(jobs_d: pd.DataFrame, s: RtoSettings) -> pd.DataFrame:
    r = jobs_d[jobs_d["rated"]]
    names = name_order(r["name"].unique())

    def agg(x):
        act, std = x["act"].sum(), x["std_total"].sum()
        tact, tstd = x["tsec"].sum(), x["std_t"].sum()
        mact, mstd = x["msec"].sum(), x["std_m"].sum()
        return pd.Series({"jobs": len(x), "avg_parcels": x["parcels"].mean(), "avg_drugs": x["dc"].mean(), "avg_units": x["units"].mean(),
                          "act": act, "std": std, "median_min": x["act"].median() / 60,
                          "eff_all": std / act if act > 0 else np.nan, "eff_transfer": tstd / tact if tact > 0 else np.nan, "eff_merge": mstd / mact if mact > 0 else np.nan})
    t = r.groupby("name").apply(agg).reindex(names) if len(r) else pd.DataFrame(columns=["jobs"])
    if len(r):
        tot = agg(r).to_frame().T; tot.index = ["Team"]
        show = t["jobs"] >= s.min_jobs
        for c in ("eff_all", "eff_transfer", "eff_merge"):
            t[c] = t[c].where(show)
        t = pd.concat([t, tot])
        t["not_rated"] = jobs_d[jobs_d["status_text"].isin(NOT_RATED_P2)].groupby("name").size().reindex(t.index).fillna(0).astype(int)
        t.loc["Team", "not_rated"] = int(jobs_d["status_text"].isin(NOT_RATED_P2).sum())
    return t


def period_matrices(u: pd.DataFrame, names: list[str], period: str, min_n: int) -> dict:
    periods = sorted(u[period].dropna().unique())
    by = summarize(u, ["name", period], min_n); by_p = summarize(u, [period], min_n); by_n = summarize(u, "name", min_n)
    tot = summarize(u, [], min_n).iloc[0]
    out = {}
    for col in ["efficiency", "n", "hours"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["Team"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


def flags_table(sort_d: pd.DataFrame, jobs_d: pd.DataFrame) -> pd.DataFrame:
    f = jobs_d[jobs_d["status_text"].isin(NOT_RATED_P2)].sort_values("start")
    return f[["name", "carton", "start", "end", "act", "std_total", "parcels", "dc", "units", "status_text"]]


def status_counts(sort_d: pd.DataFrame, jobs_d: pd.DataFrame) -> pd.DataFrame:
    a = sort_d["status_text"].value_counts().rename("Parcels")
    b = jobs_d["status_text"].value_counts().rename("Carton jobs")
    return pd.concat([a, b], axis=1).fillna(0).astype(int).sort_values("Parcels", ascending=False)


def suggested(sort_d: pd.DataFrame, jobs_d: pd.DataFrame) -> dict:
    """What the loaded rated data would fit to (least squares on rated jobs, mean gap on rated parcels)."""
    out = {}
    r1 = sort_d[sort_d["rated"]]
    if len(r1):
        out["p1_std"] = float(r1["gap"].mean())
    r = jobs_d[jobs_d["rated"]]
    if len(r) >= 8:
        one = np.ones(len(r)); u = r["units"].fillna(0).values
        for key, y, cols in (("t", r["tsec"].values, [one, r["parcels"].values, u]), ("m", r["msec"].values, [one, r["dc"].values, u])):
            X = np.column_stack(cols)
            out[key] = np.linalg.lstsq(X, y, rcond=None)[0].tolist()
    return out


def rated_shares(sort_d: pd.DataFrame, jobs_d: pd.DataFrame) -> dict:
    e1 = sort_d["status_text"].isin(["Rated", "Not rated: too long", "Not rated: zero gap"]).sum()
    e2 = jobs_d["status_text"].isin(["Rated"] + NOT_RATED_P2).sum()
    return {"p1": sort_d["rated"].sum() / e1 if e1 else np.nan, "p2": jobs_d["rated"].sum() / e2 if e2 else np.nan}


def data_checks(sort_d: pd.DataFrame, jobs_d: pd.DataFrame) -> dict:
    return {"Parcel stamps read": int(len(sort_d)), "Parcels rated": int(sort_d["rated"].sum()),
            "Carton jobs read": int(len(jobs_d)), "Carton jobs rated": int(jobs_d["rated"].sum()),
            "Jobs with merge items read": int(jobs_d["dc"].notna().sum()) if len(jobs_d) else 0,
            "Parcel stamps from": str(sort_d["ts"].min()) if len(sort_d) else "-", "Parcel stamps to": str(sort_d["ts"].max()) if len(sort_d) else "-",
            "Jobs from": str(jobs_d["start"].min()) if len(jobs_d) else "-", "Jobs to": str(jobs_d["start"].max()) if len(jobs_d) else "-"}
