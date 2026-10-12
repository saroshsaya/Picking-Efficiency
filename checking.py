"""Checking-efficiency logic. Pure pandas - no Streamlit, no database.

One row per checking task:
    drug_code, type, cub_rack, qty_checked, checked_by, created_at (task started), time (task finished)

Activities: ID checking = ID_CUBICLE / ID_RACK; Quantity checking = ST / LT.
Rules (same as the Excel workbook):
 * checking seconds = time - created_at
 * standard seconds: ID checking = one fixed time; quantity checking = a + b x LN(1 + quantity)
 * a task is RATED when it has both timestamps, min_sec <= seconds <= max_sec, and (quantity checks) a quantity
 * tasks over the limit are flagged and not rated; tasks with a missing timestamp are not considered
 * efficiency = sum(standard seconds) / sum(actual seconds) over rated tasks
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

import config
from efficiency import exec_name

ID_TYPES = ["ID_CUBICLE", "ID_RACK"]
QTY_TYPES = ["ST", "LT"]
STATUS_OVER = "Not rated: over the time limit (flagged)"
NOT_RATED_ORDER = [STATUS_OVER, "Not rated: missing timestamp", "Not rated: no quantity", "Not rated: no time elapsed"]


@dataclass
class CheckSettings:
    id_std: float = config.CHECK_ID_STD_SEC
    a: float = config.CHECK_QTY_A
    b: float = config.CHECK_QTY_B
    allowance: float = 0.0
    min_sec: float = 1
    max_sec: float = config.CHECK_MAX_SEC
    min_show: int = 0
    low_sample: int = 20


def rate_tasks(df: pd.DataFrame, s: CheckSettings) -> pd.DataFrame:
    d = df.copy()
    for c in ["created_at", "time"]:
        d[c] = pd.to_datetime(d[c], errors="coerce")
    d["qty_checked"] = pd.to_numeric(d["qty_checked"], errors="coerce")
    d = d.sort_values("created_at", kind="mergesort").reset_index(drop=True)
    d["task"] = np.arange(1, len(d) + 1)
    d["name"] = d["checked_by"].map(lambda x: exec_name(x) if isinstance(x, str) else "(unknown)")
    d["activity"] = np.where(d["type"].isin(ID_TYPES), "ID checking", np.where(d["type"].isin(QTY_TYPES), "Quantity checking", "Other"))
    d["dur"] = (d["time"] - d["created_at"]).dt.total_seconds().round()
    std = np.where(d["activity"] == "ID checking", s.id_std,
                   np.where(d["qty_checked"].notna(), s.a + s.b * np.log1p(d["qty_checked"].fillna(0)), np.nan))
    d["std_sec"] = std * (1 + s.allowance)
    d["status"] = np.select(
        [d["time"].isna() | d["created_at"].isna(), d["dur"] < s.min_sec, d["dur"] > s.max_sec, np.isnan(d["std_sec"])],
        ["Not rated: missing timestamp", "Not rated: no time elapsed", STATUS_OVER, "Not rated: no quantity"], default="Rated")
    d["rated"] = d["status"] == "Rated"
    d["date"] = d["created_at"].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["act_rated"] = np.where(d["rated"], d["dur"], 0.0)
    d["std_rated"] = np.where(d["rated"], d["std_sec"], 0.0)
    return d


def checker_order(d: pd.DataFrame) -> list[str]:
    return d.groupby("name").size().sort_values(ascending=False).index.tolist()


def summarize(d: pd.DataFrame, s: CheckSettings, keys, apply_min_show: bool = True) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    g = d.assign(_all=1).groupby(keys if keys else ["_all"])
    t = g.agg(tasks_all=("task", "size"), tasks_rated=("rated", "sum"), act_sec=("act_rated", "sum"), std_sec=("std_rated", "sum"),
              over_limit=("status", lambda x: int((x == STATUS_OVER).sum())))
    t["tasks_not_rated"] = t["tasks_all"] - t["tasks_rated"]
    ok = (t["tasks_rated"] > 0) & (t["act_sec"] > 0)
    show = ok & ((t["tasks_rated"] >= s.min_show) if apply_min_show else True)
    t["sec_per_task"] = np.where(ok, t["act_sec"] / t["tasks_rated"].where(ok, 1), np.nan)
    t["std_per_task"] = np.where(ok, t["std_sec"] / t["tasks_rated"].where(ok, 1), np.nan)
    t["efficiency"] = np.where(show, t["std_sec"] / t["act_sec"].where(ok, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def overview_table(d: pd.DataFrame, s: CheckSettings) -> pd.DataFrame:
    t = summarize(d, s, "name", apply_min_show=False).reindex(checker_order(d))
    tot = summarize(d, s, [], apply_min_show=False); tot.index = ["All checkers"]
    t = pd.concat([t, tot])
    for label, col in [("ID checking", "id"), ("Quantity checking", "qty")]:
        sub = d[d["activity"] == label]
        by = summarize(sub, s, "name", apply_min_show=False) if len(sub) else pd.DataFrame(columns=["tasks_rated", "efficiency"])
        allr = summarize(sub, s, [], apply_min_show=False) if len(sub) else None
        t[f"{col}_rated"] = by["tasks_rated"].reindex(t.index).fillna(0).astype(int) if len(sub) else 0
        t[f"{col}_eff"] = by["efficiency"].reindex(t.index) if len(sub) else np.nan
        if allr is not None:
            t.loc["All checkers", f"{col}_rated"] = int(allr["tasks_rated"].iloc[0]); t.loc["All checkers", f"{col}_eff"] = allr["efficiency"].iloc[0]
    t["verdict"] = np.where(t["efficiency"].isna(), "-", np.where(t["efficiency"] >= 1, "Above average", "Below average"))
    return t


def period_matrices(d: pd.DataFrame, s: CheckSettings, period: str) -> dict:
    names = checker_order(d)
    periods = sorted(d[period].dropna().unique())
    by = summarize(d, s, ["name", period]); by_p = summarize(d, s, [period]); by_n = summarize(d, s, "name")
    tot = summarize(d, s, []).iloc[0]
    out = {}
    for col in ["efficiency", "tasks_rated", "sec_per_task", "tasks_all"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["All checkers"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    f = d[d["status"] != "Rated"].sort_values("created_at")
    return f[["task", "name", "type", "activity", "qty_checked", "created_at", "time", "dur", "std_sec", "status"]]


def standard_samples(s: CheckSettings) -> pd.DataFrame:
    rows = [("ID checking (any)", None, s.id_std * (1 + s.allowance))]
    rows += [("Quantity checking", q, (s.a + s.b * np.log1p(q)) * (1 + s.allowance)) for q in [1, 10, 50, 150, 600, 2500]]
    return pd.DataFrame(rows, columns=["Task", "Quantity", "Standard seconds"])


def data_checks(d: pd.DataFrame) -> dict:
    return {"Tasks read": int(len(d)), "Rated": int(d["rated"].sum()),
            **{k: int((d["status"] == k).sum()) for k in NOT_RATED_ORDER}, "Data from": str(d["created_at"].min()), "Data to": str(d["created_at"].max())}
