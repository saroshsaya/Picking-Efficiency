"""Packing-efficiency logic. Pure pandas - no Streamlit, no database.

One row per packed order:
    id, packer, TGR, check_start, TGS, label_time, distinct_sku_count, total_item_count

TGR = order assembled, check_start = packer picked it up (start of packing),
TGS = Advance clicked (end of packing), label_time = label printed.

Rules (same as the Excel workbook):
 * packing seconds = TGS - check_start
 * standard seconds = (a + b*items + c*distinct SKUs) * (1 + allowance)
 * an order is RATED when it has a start time, item counts, and
   min_sec <= packing seconds <= max_sec
 * efficiency = sum(standard seconds) / sum(packing seconds) over rated orders
Queue wait (TGR -> check_start) and label delay (TGS -> label_time) are tracked separately.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

import config
from efficiency import exec_order as _unused  # noqa: F401  (keeps import style consistent)
from efficiency import exec_name

NOT_RATED = ["Not rated: too long", "Not rated: too fast", "Not rated: end before start"]


@dataclass
class PackSettings:
    study_sec_per_item: float = config.PACK_STUDY_SEC_PER_ITEM
    a: float = config.PACK_STD_A
    b: float = config.PACK_STD_B
    c: float = config.PACK_STD_C
    allowance: float = 0.0
    min_sec: float = 10
    max_sec: float = 900
    min_show: int = 0
    low_sample: int = 20
    max_wait_h: float = 12
    long_wait_min: float = 180
    label_thr_s: float = 60
    label_cap_h: float = 12


def rate_orders(df: pd.DataFrame, s: PackSettings) -> pd.DataFrame:
    d = df.copy()
    for c in ["TGR", "check_start", "TGS", "label_time"]:
        d[c] = pd.to_datetime(d[c], errors="coerce")
    for c in ["total_item_count", "distinct_sku_count"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["name"] = d["packer"].map(lambda x: exec_name(x) if isinstance(x, str) else "(unknown)")
    d["dur"] = (d["TGS"] - d["check_start"]).dt.total_seconds().round()
    d["std_sec"] = (s.a + s.b * d["total_item_count"] + s.c * d["distinct_sku_count"]) * (1 + s.allowance)
    d["status"] = np.select(
        [d["check_start"].isna(), d["TGS"].isna(), d["total_item_count"].isna() | d["distinct_sku_count"].isna(),
         d["dur"] <= 0, d["dur"] < s.min_sec, d["dur"] > s.max_sec],
        ["Not rated: no start time", "Not rated: no end time", "Not rated: no item counts",
         "Not rated: end before start", "Not rated: too fast", "Not rated: too long"],
        default="Rated")
    d["rated"] = d["status"] == "Rated"
    d["wait_min"] = ((d["check_start"] - d["TGR"]).dt.total_seconds() / 60).round(2)
    d["label_delay"] = (d["label_time"] - d["TGS"]).dt.total_seconds().round()
    d["date"] = d["TGS"].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["hour_assembled"] = d["TGR"].dt.hour
    d["hour_advanced"] = d["TGS"].dt.hour
    d["act_rated"] = np.where(d["rated"], d["dur"], 0.0)
    d["std_rated"] = np.where(d["rated"], d["std_sec"], 0.0)
    d["items_rated"] = np.where(d["rated"], d["total_item_count"], 0.0)
    return d


def packer_order(d: pd.DataFrame) -> list[str]:
    return d.groupby("name").size().sort_values(ascending=False).index.tolist()


def summarize(d: pd.DataFrame, s: PackSettings, keys, apply_min_show: bool = True) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    g = d.assign(_all=1).groupby(keys if keys else ["_all"])
    t = g.agg(orders_all=("id", "size"), orders_rated=("rated", "sum"), items_rated=("items_rated", "sum"),
              act_sec=("act_rated", "sum"), std_sec=("std_rated", "sum"))
    t["orders_not_rated"] = t["orders_all"] - t["orders_rated"]
    ok = (t["orders_rated"] > 0) & (t["act_sec"] > 0)
    show = ok & ((t["orders_rated"] >= s.min_show) if apply_min_show else True)
    t["sec_per_order"] = np.where(ok, t["act_sec"] / t["orders_rated"].where(ok, 1), np.nan)
    t["std_per_order"] = np.where(ok, t["std_sec"] / t["orders_rated"].where(ok, 1), np.nan)
    t["sec_per_item"] = np.where(ok & (t["items_rated"] > 0), t["act_sec"] / t["items_rated"].where(t["items_rated"] > 0, 1), np.nan)
    t["efficiency"] = np.where(show, t["std_sec"] / t["act_sec"].where(ok, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def overview_table(d: pd.DataFrame, s: PackSettings) -> pd.DataFrame:
    t = summarize(d, s, "name", apply_min_show=False).reindex(packer_order(d))
    tot = summarize(d, s, [], apply_min_show=False); tot.index = ["All packers"]
    t = pd.concat([t, tot])
    flagged = d[d["status"].isin(NOT_RATED)].groupby("name").size()
    t["orders_flagged"] = flagged.reindex(t.index).fillna(0).astype(int)
    t.loc["All packers", "orders_flagged"] = int(d["status"].isin(NOT_RATED).sum())
    t["study_sec_per_item"] = s.study_sec_per_item
    t["diff_vs_study"] = t["sec_per_item"] - t["study_sec_per_item"]
    t["verdict"] = np.where(t["efficiency"].isna(), "-", np.where(t["efficiency"] >= 1, "Above average", "Below average"))
    return t


def period_matrices(d: pd.DataFrame, s: PackSettings, period: str) -> dict:
    names = packer_order(d)
    periods = sorted(d[period].dropna().unique())
    by = summarize(d, s, ["name", period]); by_p = summarize(d, s, [period]); by_n = summarize(d, s, "name")
    tot = summarize(d, s, []).iloc[0]
    out = {}
    for col in ["efficiency", "orders_rated", "sec_per_order", "orders_all"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["All packers"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    f = d[d["status"] != "Rated"].sort_values("TGS")
    return f[["id", "name", "date", "check_start", "TGS", "dur", "total_item_count", "distinct_sku_count", "std_sec", "status"]]


# ------------------------------------------------------------- bottlenecks
def _bn(d: pd.DataFrame, s: PackSettings, key: str) -> pd.DataFrame:
    maxw = s.max_wait_h * 60
    w = d["wait_min"]; ld = d["label_delay"]; cap = s.label_cap_h * 3600
    x = pd.DataFrame({
        key: d[key],
        "orders": 1,
        "wait_counted": np.where((w >= 0) & (w <= maxw), w, np.nan),
        "long_wait": ((w > s.long_wait_min) & (w <= maxw)).astype(int),
        "next_day_wait": (w > maxw).astype(int),
        "with_label": d["label_time"].notna().astype(int),
        "label_delay": ((ld > s.label_thr_s) & (ld <= cap)).astype(int),
        "label_next_day": (ld > cap).astype(int),
        "label_min": np.where((ld > s.label_thr_s) & (ld <= cap), ld / 60, 0.0),
    })
    g = x.groupby(key)
    t = g.agg(orders=("orders", "sum"), avg_wait=("wait_counted", "mean"), wait_hours=("wait_counted", lambda v: v.sum() / 60),
              long_wait=("long_wait", "sum"), next_day_wait=("next_day_wait", "sum"), with_label=("with_label", "sum"),
              label_delay=("label_delay", "sum"), label_next_day=("label_next_day", "sum"), label_min=("label_min", "sum"))
    t["pct_long_wait"] = t["long_wait"] / t["orders"]
    t["pct_label_delay"] = np.where(t["with_label"] > 0, t["label_delay"] / t["with_label"].where(t["with_label"] > 0, 1), np.nan)
    t["avg_label_delay_min"] = np.where(t["label_delay"] > 0, t["label_min"] / t["label_delay"].where(t["label_delay"] > 0, 1), np.nan)
    t["no_label"] = t["orders"] - t["with_label"]
    return t


def bottlenecks(d: pd.DataFrame, s: PackSettings) -> dict:
    return {"week": _bn(d, s, "week"), "date": _bn(d, s, "date"),
            "hour_assembled": _bn(d, s, "hour_assembled"), "hour_advanced": _bn(d, s, "hour_advanced")}


def standard_samples(s: PackSettings) -> pd.DataFrame:
    rows = [("Small", 1, 1), ("Small", 3, 2), ("Typical", 14, 3.5), ("Medium", 25, 5), ("Large", 50, 8), ("Very large", 100, 12)]
    return pd.DataFrame([{"Sample order": n, "Items": i, "SKUs": k, "Standard seconds": (s.a + s.b * i + s.c * k) * (1 + s.allowance)} for n, i, k in rows])


def data_checks(d: pd.DataFrame) -> dict:
    return {
        "Orders read": int(len(d)),
        "With item and SKU counts": int(d["total_item_count"].notna().sum()),
        "Rated": int(d["rated"].sum()),
        **{k: int((d["status"] == k).sum()) for k in sorted(d["status"].unique()) if k != "Rated"},
        "Orders with a label time": int(d["label_time"].notna().sum()),
        "Data from": str(d["TGS"].min()), "Data to": str(d["TGS"].max()),
    }
