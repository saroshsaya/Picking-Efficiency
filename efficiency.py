"""Picking-efficiency logic. Pure pandas, no Streamlit, no database.

This module is the part to hand to anyone who needs the business rules.

Input: one row per make-list LINE with columns
    drug_code, quantity, picker, make_list_created_at, updated_at
(optional: subtraction_time). Times are timezone-naive datetimes; empty = NaT.

Rules (identical to the Excel workbook):
 1. LINES -> MAKE LISTS. Lines of the same picker whose make_list_created_at are
    within `merge_seconds` of the previous line belong to one make list.
 2. A line is PICKED if it has a scan time (updated_at). Pieces = sum(quantity).
 3. Picking minutes of a list:
      normal list           : creation -> last scan
      flagged list (long wait before first scan, or partial list)
                            : first scan -> last scan + lead-in allowance
 4. A list is RATED if it has >= min_pieces pieces and its minutes <= max_minutes.
 5. Efficiency = benchmark seconds per item / actual seconds per item, pooled over
    the rated lists (add pieces and minutes first, divide once).
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from config import LOGIN_NAMES


@dataclass
class Settings:
    benchmark_sec: float = 4.78        # recorded average (sec/item)
    allowance: float = 0.0             # 0.10 = 10% extra time allowed
    basis: int = 1                     # 1 = creation->last scan, 2 = first->last scan
    min_pieces: int = 60               # lists below this are not rated
    partial_share: float = 0.90        # below this share of lines picked = partial list
    long_wait_min: float = 10.0        # creation->first scan above this = long wait
    lead_allowance_min: float = 1.0    # added to lists timed from first scan
    max_minutes: float = 90.0          # lists longer than this are not rated
    min_pieces_show: int = 0           # min rated pieces to show a period cell
    low_sample: int = 150              # grey out cells with fewer rated pieces
    max_codes: int = 48                # max distinct drug codes per make list
    merge_seconds: float = 2.0         # lines this close = same make list

    @property
    def bench(self) -> float:
        return self.benchmark_sec * (1 + self.allowance)


def exec_name(login: str) -> str:
    if login in LOGIN_NAMES:
        return LOGIN_NAMES[login]
    return login.split("@")[0] if "@" in login else str(login)


# ---------------------------------------------------------------- lines -> lists
def build_lists(lines: pd.DataFrame, merge_seconds: float = 2.0):
    """Return (lists, lines_with_ml_id)."""
    d = lines.copy()
    d = d.dropna(subset=["make_list_created_at"])
    d = d.sort_values(["picker", "make_list_created_at"], kind="mergesort").reset_index(drop=True)
    prev = d.groupby("picker")["make_list_created_at"].shift()
    gap = (d["make_list_created_at"] - prev).dt.total_seconds()
    new = prev.isna() | (gap > merge_seconds)
    d["_grp"] = new.cumsum()

    lists = d.groupby("_grp").agg(
        picker=("picker", "first"),
        start=("make_list_created_at", "min"),
        lines=("drug_code", "size"),
        picked_lines=("updated_at", "count"),
        pieces=("quantity", "sum"),
        first=("updated_at", "min"),
        last=("updated_at", "max"),
        distinct_codes=("drug_code", "nunique"),
    ).reset_index()
    lists = lists.sort_values(["start", "picker"]).reset_index(drop=True)
    lists["ml_id"] = ["ML%05d" % (i + 1) for i in range(len(lists))]
    d = d.merge(lists[["_grp", "ml_id"]], on="_grp", how="left").drop(columns="_grp")
    lists = lists.drop(columns="_grp")
    lists["exec"] = lists["picker"].map(exec_name)
    return lists, d


def median_lead_in(lists: pd.DataFrame, s: Settings) -> float:
    """Median creation->first-scan gap of normal lists (used as the lead-in allowance)."""
    picked = lists["picked_lines"] > 0
    share = lists["picked_lines"] / lists["lines"].where(lists["lines"] > 0, np.nan)
    normal = lists[picked & (lists["pieces"] >= s.min_pieces) & (share >= s.partial_share)]
    lead = (normal["first"] - normal["start"]).dt.total_seconds() / 60
    return float(lead.median()) if len(lead) else 1.0


# ------------------------------------------------------------------ rate lists
def rate_lists(lists: pd.DataFrame, s: Settings) -> pd.DataFrame:
    d = lists.copy()
    d["lead_in"] = (d["first"] - d["start"]).dt.total_seconds() / 60
    d["created_to_last"] = (d["last"] - d["start"]).dt.total_seconds() / 60
    d["first_to_last"] = (d["last"] - d["first"]).dt.total_seconds() / 60
    d["share"] = np.where(d["lines"] > 0, d["picked_lines"] / d["lines"].where(d["lines"] > 0, 1), 0.0)
    picked = d["picked_lines"] > 0
    d["picked"] = picked
    d["long_wait"] = picked & (d["lead_in"] > s.long_wait_min)
    d["partial"] = picked & (d["share"] < s.partial_share)
    d["over_codes"] = d["distinct_codes"] > s.max_codes
    d["from_first"] = picked & ((s.basis == 2) | d["long_wait"] | d["partial"])
    allow = s.lead_allowance_min if s.basis == 1 else 0.0
    d["minutes"] = np.where(d["from_first"], d["first_to_last"] + allow, d["created_to_last"])
    d.loc[~picked, "minutes"] = np.nan

    d["status"] = np.select(
        [~picked, d["pieces"] < s.min_pieces, d["minutes"] > s.max_minutes],
        ["Nothing picked", "Not rated: too small", "Not rated: too long"],
        default="Rated",
    )
    d["rated"] = d["status"] == "Rated"
    d["flags"] = (
        np.where(d["long_wait"], "Long wait; ", "")
        + np.where(d["partial"], "Partial list; ", "")
        + np.where(d["over_codes"], f"Over {s.max_codes} drug codes; ", "")
        + np.where(d["status"] == "Not rated: too long", "Too long; ", "")
    )
    d["flags"] = d["flags"].str.strip()
    d["flagged"] = picked & (d["flags"] != "")

    d["date"] = d["start"].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["bench_minutes"] = d["pieces"] * s.bench / 60
    d["rated_minutes"] = np.where(d["rated"], d["minutes"], 0.0)
    d["rated_bench_minutes"] = np.where(d["rated"], d["bench_minutes"], 0.0)
    d["rated_pieces"] = np.where(d["rated"], d["pieces"], 0)
    return d


# ------------------------------------------------------------------- summaries
def summarize(d: pd.DataFrame, s: Settings, keys) -> pd.DataFrame:
    """Pooled summary per group. `keys` is a column name or list; [] = one total row."""
    keys = [keys] if isinstance(keys, str) else list(keys)
    g = d.assign(_all=1).groupby(keys if keys else ["_all"])
    t = g.agg(
        lists_picked=("picked", "sum"),
        pieces_all=("pieces", "sum"),
        lists_rated=("rated", "sum"),
        pieces_rated=("rated_pieces", "sum"),
        minutes_rated=("rated_minutes", "sum"),
        bench_minutes_rated=("rated_bench_minutes", "sum"),
        lists_flagged=("flagged", "sum"),
    )
    t["pieces_not_rated"] = t["pieces_all"] - t["pieces_rated"]
    ok = (t["pieces_rated"] > 0) & (t["minutes_rated"] > 0)
    t["sec_per_item"] = np.where(ok, t["minutes_rated"] * 60 / t["pieces_rated"].where(ok, 1), np.nan)
    t["efficiency"] = np.where(ok & (t["pieces_rated"] >= s.min_pieces_show),
                               t["bench_minutes_rated"] / t["minutes_rated"].where(ok, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def exec_order(d: pd.DataFrame) -> list[str]:
    """Executives with picked work, in the order of config.LOGIN_NAMES, then others."""
    present = set(d.loc[d["picked"], "exec"])
    ordered = [n for n in LOGIN_NAMES.values() if n in present]
    return ordered + sorted(present - set(ordered))


def overview_table(d: pd.DataFrame, s: Settings) -> pd.DataFrame:
    t = summarize(d, s, "exec")
    rows = exec_order(d)
    t = t.reindex(rows)
    tot = summarize(d, s, [])
    tot.index = ["All executives"]
    t = pd.concat([t, tot])
    t["benchmark_sec"] = s.bench
    t["difference_sec"] = t["sec_per_item"] - t["benchmark_sec"]
    t["verdict"] = np.where(t["efficiency"].isna(), "-",
                            np.where(t["efficiency"] >= 1, "Above average", "Below average"))
    return t


def period_matrices(d: pd.DataFrame, s: Settings, period: str):
    """Executives x periods (+ Overall column, + All executives row).
    Returns dict of DataFrames: efficiency, pieces_rated, sec_per_item, pieces_all."""
    execs = exec_order(d)
    periods = sorted(d[period].dropna().unique())
    by = summarize(d, s, ["exec", period])
    by_p = summarize(d, s, [period])
    by_e = summarize(d, s, "exec")
    tot = summarize(d, s, []).iloc[0]
    out = {}
    for col in ["efficiency", "pieces_rated", "sec_per_item", "pieces_all"]:
        m = by[col].unstack(period).reindex(index=execs, columns=periods)
        m["Overall"] = by_e[col].reindex(execs)
        allrow = by_p[col].reindex(periods).tolist() + [tot[col]]
        m.loc["All executives"] = allrow
        out[col] = m
    return out


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    f = d[d["flagged"]].sort_values("start")
    return f[["ml_id", "exec", "start", "lead_in", "first", "last", "lines", "picked_lines",
              "share", "pieces", "distinct_codes", "flags", "status"]]


def data_checks(lines: pd.DataFrame, lists_rated: pd.DataFrame) -> dict:
    out = {
        "Lines": int(len(lines)),
        "Lines in make lists": int(lists_rated["lines"].sum()),
        "Pieces": int(lines["quantity"].sum()),
        "Pieces in make lists": int(lists_rated["pieces"].sum()),
        "Lines with a scan time": int(lines["updated_at"].notna().sum()),
        "Make lists": int(len(lists_rated)),
        "Lists with nothing picked": int((~lists_rated["picked"]).sum()),
        "Data from": str(lists_rated["start"].min()),
        "Data to": str(lists_rated["start"].max()),
    }
    if "subtraction_time" in lines.columns:
        both = lines["updated_at"].notna()
        out["subtraction_time equals updated_at (lines)"] = int((both & (lines["subtraction_time"] == lines["updated_at"])).sum())
        out["subtraction_time later than updated_at (lines)"] = int((both & (lines["subtraction_time"] > lines["updated_at"])).sum())
    return out
