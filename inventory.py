"""Inventory (slot verification) efficiency logic. Pure pandas - no Streamlit, no database.

One row per verification of a drug code (table testing_details, plus quantity from tbt_differences):
    drug_code, checked_by, verify_start, verify_finish, checked_time, quantity_stated, q_found

Rules (same as Inventory_Efficiency_v2.xlsx):
 * duration = checked_time - verify_start   (first value ticked in VERIFY -> quantity found entered)
 * the person is checked_by (the same person verifies and checks)
 * standard seconds = one fixed time per verification (quantity does not explain the time)
 * RATED when a checker and both stamps exist, end is after start, same calendar day (optional) and
   duration <= the cap
 * efficiency = sum(standard seconds) / sum(actual seconds) over rated verifications
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

import config
from efficiency import exec_name

NOT_RATED = ["Not rated: overnight", "Not rated: too long", "Not rated: end before start", "Not rated: missing timestamp"]


@dataclass
class InvSettings:
    std_sec: float = config.INV_STD_SEC
    max_sec: float = config.INV_MAX_SEC
    exclude_overnight: bool = True
    min_show: int = 10
    low_sample: int = 10


def rate_verifications(df: pd.DataFrame, s: InvSettings) -> pd.DataFrame:
    d = df.copy()
    for c in ["verify_start", "verify_finish", "checked_time"]:
        d[c] = pd.to_datetime(d[c], errors="coerce") if c in d else pd.NaT
    for c in ["quantity_stated", "q_found"]:
        d[c] = pd.to_numeric(d[c], errors="coerce") if c in d else np.nan
    has = d["checked_by"].notna() & (d["checked_by"].astype(str).str.strip() != "")
    d["name"] = d["checked_by"].map(lambda x: exec_name(x) if isinstance(x, str) and x.strip() else "(no checker)")
    d["dur"] = (d["checked_time"] - d["verify_start"]).dt.total_seconds().round()
    d["same_day"] = d["verify_start"].dt.date == d["checked_time"].dt.date
    d["status_text"] = np.select(
        [~has, d["verify_start"].isna() | d["checked_time"].isna(), d["dur"] <= 0, bool(s.exclude_overnight) & ~d["same_day"], d["dur"] > s.max_sec],
        ["Not rated: no checker", "Not rated: missing timestamp", "Not rated: end before start", "Not rated: overnight", "Not rated: too long"],
        default="Rated")
    d["rated"] = d["status_text"] == "Rated"
    d["std_sec"] = s.std_sec
    d["date"] = d["verify_start"].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["act_rated"] = np.where(d["rated"], d["dur"], 0.0)
    d["std_rated"] = np.where(d["rated"], d["std_sec"], 0.0)
    d["q_rated"] = np.where(d["rated"], d["q_found"].fillna(0), 0.0)
    return d


def checker_order(d: pd.DataFrame) -> list[str]:
    return d[d["rated"]].groupby("name").size().sort_values(ascending=False).index.tolist()


def summarize(d: pd.DataFrame, s: InvSettings, keys, apply_min_show: bool = True) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    g = d.assign(_all=1).groupby(keys if keys else ["_all"])
    t = g.agg(jobs_rated=("rated", "size"), act_sec=("act_rated", "sum"), std_sec=("std_rated", "sum"), q_sum=("q_rated", "sum"))
    ok = (t["jobs_rated"] > 0) & (t["act_sec"] > 0)
    show = ok & ((t["jobs_rated"] >= s.min_show) if apply_min_show else True)
    t["sec_per_job"] = np.where(ok, t["act_sec"] / t["jobs_rated"].where(ok, 1), np.nan)
    t["avg_qty"] = np.where(ok, t["q_sum"] / t["jobs_rated"].where(ok, 1), np.nan)
    t["efficiency"] = np.where(show, t["std_sec"] / t["act_sec"].where(ok, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def overview_table(d: pd.DataFrame, s: InvSettings) -> pd.DataFrame:
    r = d[d["rated"]]
    t = summarize(r, s, "name", apply_min_show=True).reindex(checker_order(d))
    t["median_sec"] = r.groupby("name")["dur"].median().reindex(t.index)
    t["not_rated"] = d[d["status_text"].isin(NOT_RATED)].groupby("name").size().reindex(t.index).fillna(0).astype(int)
    tot = summarize(r, s, [], apply_min_show=False); tot.index = ["All checkers"]
    tot["median_sec"] = r["dur"].median() if len(r) else np.nan
    tot["not_rated"] = int(d["status_text"].isin(NOT_RATED).sum())
    t = pd.concat([t, tot])
    t["verdict"] = np.where(t["efficiency"].isna(), "-", np.where(t["efficiency"] >= 1, "Above team standard", "Below team standard"))
    return t


def period_matrices(d: pd.DataFrame, s: InvSettings, period: str) -> dict:
    r = d[d["rated"]]
    names = checker_order(d)
    periods = sorted(r[period].dropna().unique())
    by = summarize(r, s, ["name", period]); by_p = summarize(r, s, [period]); by_n = summarize(r, s, "name")
    tot = summarize(r, s, []).iloc[0]
    out = {}
    for col in ["efficiency", "jobs_rated", "sec_per_job"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["All checkers"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    f = d[d["status_text"].isin(NOT_RATED)].sort_values("verify_start")
    return f[["name", "drug_code", "verify_start", "checked_time", "dur", "q_found", "status_text"]]


def suggested_standard(d: pd.DataFrame):
    r = d[d["rated"]]
    return float(r["dur"].mean()) if len(r) else None


def quantity_r2(d: pd.DataFrame):
    r = d[d["rated"] & d["q_found"].notna()]
    if len(r) < 10 or r["q_found"].nunique() < 2:
        return None
    return float(np.corrcoef(r["q_found"], r["dur"])[0, 1] ** 2)


def status_counts(d: pd.DataFrame) -> pd.DataFrame:
    return d.groupby("status_text").size().sort_values(ascending=False).rename("Rows").to_frame()


def data_checks(d: pd.DataFrame) -> dict:
    return {"Verifications read": int(len(d)), "Rated": int(d["rated"].sum()),
            **{k: int(v) for k, v in d["status_text"].value_counts().items() if k != "Rated"},
            "With quantity found": int(d["q_found"].notna().sum()),
            "Data from": str(d["verify_start"].min()), "Data to": str(d["verify_start"].max())}
