"""DTO (customer return via courier) efficiency logic. Pure pandas - no Streamlit, no database.

One row per DTO (table dto_shipment_tracking):
    awb, accepted_by, accepted_at (Accept Return), spt (Start Return Processing), trh (Return Handled)

Rules (same as DTO_Efficiency.xlsx):
 * duration = trh - spt  (Start to End; courier time and restocking after Return Handled are not part of it)
 * the person rated is the login that accepted the DTO (accepted_by)
 * standard seconds = DTO_STD_SEC x (1 + allowance): one flat figure for every DTO (the time study average)
 * a DTO is RATED when it has both stamps, the end is not before the start, and the duration is between the
   shortest and the longest time that can be rated
 * efficiency = sum(standard seconds) / sum(actual seconds) over rated DTOs
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

import config

IST = timezone(timedelta(hours=5, minutes=30))

S_RATED = "Rated"
S_NO_TIME = "Not rated: no start or end time yet"
S_NEG = "Not rated: end before start"
S_SAME = "Not rated: start and end at the same time"
S_LONG = "Not rated: too long"
NOT_RATED = [S_SAME, S_LONG, S_NEG, S_NO_TIME]


@dataclass
class DtoSettings:
    std_sec: float = config.DTO_STD_SEC
    allowance: float = 0.0          # 0.10 = every standard time 10% longer
    min_sec: float = config.DTO_MIN_SEC
    max_sec: float = config.DTO_MAX_SEC
    slow_x: float = config.DTO_SLOW_X
    min_show: int = 20              # rated DTOs a person (or the team) needs on the Overview to show a percentage
    min_show_period: int = 5        # rated DTOs needed in a week / month cell
    low_sample: int = 15            # cells built on fewer rated DTOs are shown in grey italics


# ------------------------------------------------------------------ reading the stored values
def epoch_to_ist(value):
    """Stored epoch text -> naive IST datetime to the second, or None.
    13 digits = milliseconds (older module), 10 digits = seconds (newer module).
    'NA', 'NaN', empty or any other length -> None. (Dividing every value by 1000 cuts the 10-digit ones to about 17 minutes.)"""
    if value is None:
        return None
    s = str(value).strip()
    m = re.fullmatch(r"(\d+)(\.\d+)?", s)
    if not m:
        return None
    digits = len(m.group(1))
    x = float(s)
    if digits == 13:
        x /= 1000.0
    elif digits != 10:
        return None
    return datetime.fromtimestamp(round(x), tz=IST).replace(tzinfo=None)


def clean_text(value):
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    s = str(value).strip()
    return None if s in ("", "NA", "NaN", "None", "nan") else s


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Rows as stored (epoch text) -> awb, accepted_by, accepted_at, spt, trh with IST datetimes."""
    d = raw[["awb", "accepted_by", "accepted_at", "spt", "trh"]].copy()
    for c in ("accepted_at", "spt", "trh"):
        d[c] = pd.to_datetime(d[c].map(epoch_to_ist), errors="coerce")
    d["awb"] = d["awb"].map(clean_text)
    d["accepted_by"] = d["accepted_by"].map(clean_text)
    return d


def name_of(login) -> str:
    if not isinstance(login, str) or not login.strip():
        return "(not accepted)"
    key = login.strip().lower()
    return (config.LOGIN_NAMES.get(key) or config.DTO_LOGIN_NAMES.get(key)
            or (key.split("@")[0] if "@" in key else key))


# ------------------------------------------------------------------ rating
def rate_dtos(df: pd.DataFrame, s: DtoSettings) -> pd.DataFrame:
    d = df.copy()
    for c in ["accepted_at", "spt", "trh"]:
        d[c] = pd.to_datetime(d[c], errors="coerce")
    d["name"] = d["accepted_by"].map(name_of)
    d["dur"] = (d["trh"] - d["spt"]).dt.total_seconds().round()
    missing = d["spt"].isna() | d["trh"].isna()
    d["status_text"] = np.select([missing, d["dur"] < 0, d["dur"] < s.min_sec, d["dur"] > s.max_sec],
                                 [S_NO_TIME, S_NEG, S_SAME, S_LONG], default=S_RATED)
    d["rated"] = d["status_text"] == S_RATED
    d["finished"] = d["trh"].notna()
    d["std_sec"] = s.std_sec * (1 + s.allowance)
    # the day a DTO belongs to: the day it ended; if it has not ended, the day it started or was accepted
    d["date"] = d["trh"].fillna(d["spt"]).fillna(d["accepted_at"]).dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["act_rated"] = np.where(d["rated"], d["dur"], 0.0)
    d["std_rated"] = np.where(d["rated"], d["std_sec"], 0.0)
    d["slow"] = d["rated"] & (d["dur"] > s.slow_x * d["std_sec"])
    return d


def person_order(d: pd.DataFrame) -> list[str]:
    f = d[d["finished"]]
    return f.groupby("name").size().sort_values(ascending=False).index.tolist()


def summarize(d: pd.DataFrame, s: DtoSettings, keys, min_show: int | None = None) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    if d.empty and not keys:      # nothing to total: one row of zeros, so the "Team" row can still be shown
        return pd.DataFrame({"finished": [0], "rated": [0], "act_sec": [0.0], "std_sec": [0.0],
                             "avg_sec": [np.nan], "efficiency": [np.nan], "share_rated": [np.nan]})
    g = d.assign(_all=1).groupby(keys if keys else ["_all"])
    t = g.agg(finished=("finished", "sum"), rated=("rated", "sum"), act_sec=("act_rated", "sum"), std_sec=("std_rated", "sum"))
    ok = (t["rated"] > 0) & (t["act_sec"] > 0)
    ms = s.min_show if min_show is None else min_show
    t["avg_sec"] = np.where(ok, t["act_sec"] / t["rated"].where(ok, 1), np.nan)
    t["efficiency"] = np.where(ok & (t["rated"] >= ms), t["std_sec"] / t["act_sec"].where(ok, 1), np.nan)
    t["share_rated"] = np.where(t["finished"] > 0, t["rated"] / t["finished"].where(t["finished"] > 0, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def overview_table(d: pd.DataFrame, s: DtoSettings) -> pd.DataFrame:
    f = d[d["finished"]]
    t = summarize(f, s, "name").reindex(person_order(d))
    tot = summarize(f, s, []); tot.index = ["Team"]
    t = pd.concat([t, tot])
    t["note"] = np.where(t["finished"] == 0, "No finished DTOs",
                         np.where(t["efficiency"].isna(), "Fewer than " + str(s.min_show) + " rated DTOs: efficiency not shown.", ""))
    return t


def period_matrices(d: pd.DataFrame, s: DtoSettings, period: str) -> dict:
    r = d[d["rated"]]
    if r.empty:
        return {k: pd.DataFrame() for k in ["efficiency", "rated", "avg_sec"]}
    names = person_order(d)
    periods = sorted(r[period].dropna().unique())
    mp = s.min_show_period
    by = summarize(r, s, ["name", period], min_show=mp); by_p = summarize(r, s, [period], min_show=mp)
    by_n = summarize(r, s, "name", min_show=mp)
    out = {}
    tot = summarize(r, s, [], min_show=mp).iloc[0]
    for col in ["efficiency", "rated", "avg_sec"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["Team"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    cols = ["name", "awb", "spt", "trh", "dur", "std_sec", "review"]
    f = d[d["slow"] | d["status_text"].isin([S_LONG, S_NEG])].copy()
    if f.empty:
        return pd.DataFrame(columns=cols)
    f["review"] = np.select(
        [f["status_text"] == S_LONG, f["status_text"] == S_NEG],
        ["Took " + (f["dur"] / 60).map("{:,.0f}".format) + " minutes: the start or the end may have been missed.",
         "End is before start."],
        default="Slow: " + (f["dur"] / 60).map("{:.1f}".format) + " minutes.")
    f = f.sort_values("spt")
    return f[["name", "awb", "spt", "trh", "dur", "std_sec", "review"]]


def status_counts(d: pd.DataFrame) -> pd.DataFrame:
    return d.groupby("status_text").size().sort_values(ascending=False).rename("DTOs").to_frame()


def data_checks(d: pd.DataFrame) -> dict:
    both = d["spt"].notna() & d["trh"].notna()
    awb = d["awb"].dropna()
    return {
        "DTOs read": int(len(d)),
        "With both a start and an end": int(both.sum()),
        "... start and end at exactly the same recorded time": int((both & (d["dur"] == 0)).sum()),
        "... start equals the Accept Return time": int((both & (d["spt"] == d["accepted_at"])).sum()),
        "... end equals the Accept Return time": int((both & (d["trh"] == d["accepted_at"])).sum()),
        "End before start": int((d["status_text"] == S_NEG).sum()),
        "AWB repeated": int(awb.duplicated().sum()),
        "DTOs with no AWB": int(d["awb"].isna().sum()),
        "Data from": str(d["date"].min()), "Data to": str(d["date"].max()),
    }
