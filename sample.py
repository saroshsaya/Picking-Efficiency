"""Sample-creation efficiency logic. Pure pandas - no Streamlit, no database.

One row per sample order (table testing_sample):
    packer, status, created_at, shipping_time, total_items   (distinct_skus is kept for reference only)

Rules (same as Sample_Creation_Efficiency_v2.xlsx):
 * duration = shipping_time - created_at  (the whole process, as defined by the operations lead)
 * the person rated is the PACKER - the packer does the whole process; the assembler often just clicks a button
 * standard seconds = a + b x items          (SKU count is NOT used: every item is blacked out by hand)
 * a sample is RATED when status is past, the packer is on the team list, both stamps exist, end is after
   start, it ended the same calendar day (optional rule) and items > 0
 * efficiency = sum(standard seconds) / sum(actual seconds) over rated samples
"""
from __future__ import annotations
import json
from dataclasses import dataclass
import numpy as np
import pandas as pd

import config
from efficiency import exec_name

NOT_RATED = ["Not rated: overnight", "Not rated: end before start", "Not rated: missing timestamp", "Not rated: no items"]


@dataclass
class SampleSettings:
    a: float = config.SAMPLE_STD_A
    b: float = config.SAMPLE_STD_B
    exclude_overnight: bool = True
    include_outside_team: bool = False
    min_show: int = 0
    low_sample: int = 5


def count_details(details) -> tuple[int, int]:
    """(distinct SKUs, total items) from the `details` JSON of a sample (keys = SKUs, each with a quantity)."""
    if isinstance(details, (str, bytes)):
        try:
            details = json.loads(details)
        except (ValueError, TypeError):
            return 0, 0
    if not isinstance(details, dict):
        return 0, 0
    items = 0
    for info in details.values():
        if isinstance(info, dict):
            q = info.get("quantity", info.get("Quantity"))
            try:
                items += int(float(q)) if q is not None else 0
            except (ValueError, TypeError):
                pass
    return len(details), items


def rate_samples(df: pd.DataFrame, s: SampleSettings) -> pd.DataFrame:
    d = df.copy()
    for c in ["created_at", "shipping_time"]:
        d[c] = pd.to_datetime(d[c], errors="coerce")
    d["total_items"] = pd.to_numeric(d["total_items"], errors="coerce").fillna(0)
    d["id"] = d.index
    has_packer = d["packer"].notna() & (d["packer"].astype(str).str.strip() != "")
    d["name"] = d["packer"].map(lambda x: exec_name(x) if isinstance(x, str) and x.strip() else "(no packer)")
    d["dur"] = (d["shipping_time"] - d["created_at"]).dt.total_seconds().round()
    d["same_day"] = d["created_at"].dt.date == d["shipping_time"].dt.date
    d["std_sec"] = s.a + s.b * d["total_items"]
    on_team = d["packer"].isin(config.SAMPLE_EXCLUDED_LOGINS).eq(False) | s.include_outside_team
    d["status_text"] = np.select(
        [~has_packer, ~on_team, d["status"] != "past", d["created_at"].isna() | d["shipping_time"].isna(), d["dur"] <= 0,
         bool(s.exclude_overnight) & ~d["same_day"], d["total_items"] <= 0],
        ["Not rated: no packer", "Not rated: not in team list", "Not rated: status=" + d["status"].astype(str), "Not rated: missing timestamp",
         "Not rated: end before start", "Not rated: overnight", "Not rated: no items"],
        default="Rated")
    d["rated"] = d["status_text"] == "Rated"
    d["date"] = d["created_at"].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["act_rated"] = np.where(d["rated"], d["dur"], 0.0)
    d["std_rated"] = np.where(d["rated"], d["std_sec"], 0.0)
    d["items_rated"] = np.where(d["rated"], d["total_items"], 0.0)
    return d


def packer_order(d: pd.DataFrame) -> list[str]:
    r = d[d["rated"]]
    return r.groupby("name").size().sort_values(ascending=False).index.tolist()


def summarize(d: pd.DataFrame, s: SampleSettings, keys, apply_min_show: bool = True) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    g = d.assign(_all=1).groupby(keys if keys else ["_all"])
    t = g.agg(samples_all=("rated", "size"), samples_rated=("rated", "sum"), items_rated=("items_rated", "sum"),
              act_sec=("act_rated", "sum"), std_sec=("std_rated", "sum"))
    ok = (t["samples_rated"] > 0) & (t["act_sec"] > 0)
    show = ok & ((t["samples_rated"] >= s.min_show) if apply_min_show else True)
    t["min_per_sample"] = np.where(ok, t["act_sec"] / t["samples_rated"].where(ok, 1) / 60, np.nan)
    t["sec_per_item"] = np.where(ok & (t["items_rated"] > 0), t["act_sec"] / t["items_rated"].where(t["items_rated"] > 0, 1), np.nan)
    t["efficiency"] = np.where(show, t["std_sec"] / t["act_sec"].where(ok, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def overview_table(d: pd.DataFrame, s: SampleSettings) -> pd.DataFrame:
    rated = d[d["rated"]]
    t = summarize(rated, s, "name", apply_min_show=False).reindex(packer_order(d))
    tot = summarize(rated, s, [], apply_min_show=False); tot.index = ["All packers"]
    t = pd.concat([t, tot])
    t["verdict"] = np.where(t["efficiency"].isna(), "-", np.where(t["efficiency"] >= 1, "Above team baseline", "Below team baseline"))
    return t


def period_matrices(d: pd.DataFrame, s: SampleSettings, period: str) -> dict:
    r = d[d["rated"]]
    names = packer_order(d)
    periods = sorted(r[period].dropna().unique())
    by = summarize(r, s, ["name", period]); by_p = summarize(r, s, [period]); by_n = summarize(r, s, "name")
    tot = summarize(r, s, []).iloc[0]
    out = {}
    for col in ["efficiency", "samples_rated", "min_per_sample"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["All packers"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    f = d[(d["status_text"] != "Rated") & d["status_text"].isin(NOT_RATED)].sort_values("created_at")
    return f[["name", "created_at", "shipping_time", "dur", "total_items", "std_sec", "status_text"]]


def fit_standard(d: pd.DataFrame):
    """Least-squares (a, b, r2) of duration on items over the rated samples; None if too few."""
    r = d[d["rated"]]
    if len(r) < 10 or r["total_items"].nunique() < 2:
        return None
    b, a = np.polyfit(r["total_items"], r["dur"], 1)
    r2 = float(np.corrcoef(r["total_items"], r["dur"])[0, 1] ** 2)
    return float(a), float(b), r2


def standard_samples(s: SampleSettings) -> pd.DataFrame:
    return pd.DataFrame([{"Items in sample": n, "Standard minutes": (s.a + s.b * n) / 60} for n in [10, 25, 50, 75, 100, 150, 200]])


def status_counts(d: pd.DataFrame) -> pd.DataFrame:
    return d.groupby("status_text").size().sort_values(ascending=False).rename("Rows").to_frame()


def data_checks(d: pd.DataFrame) -> dict:
    return {"Samples read": int(len(d)), "Rated": int(d["rated"].sum()),
            **{k: int(v) for k, v in d["status_text"].value_counts().items() if k != "Rated"},
            "Data from": str(d["created_at"].min()), "Data to": str(d["created_at"].max())}
