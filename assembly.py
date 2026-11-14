"""Assembly efficiency logic. Pure pandas - no Streamlit, no database.

One row per assembled order (past_orders + cancelled_orders with a TGR stamp):
    assembler (login), n_items (number of different drug codes in the order), tgr (order marked assembled),
    first_sub (the earliest inventory subtraction of that order), order_id

Rules (the same as Assembly_Efficiency.xlsx):
 * time taken = TGR - first inventory subtraction of the order (orders and subtractions are linked by phone number + order id)
 * the person rated is the assembler
 * standard seconds = fixed seconds + seconds per drug code x number of different drug codes  (quantities are not used)
 * every order is RATED unless something is missing or wrong: no assembler, no subtraction found, no drug code count,
   the first subtraction after TGR (a wrong stamp), or longer than the limit (left open / put on hold)
 * efficiency = sum(standard seconds) / sum(actual seconds) over rated orders
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

import config

S_RATED = "Rated"
S_NO_ASM = "Not rated: no assembler recorded"
S_NO_SUB = "Not rated: no inventory subtraction found"
S_NO_CODES = "Not rated: no drug code count"
S_NEG = "Not rated: first subtraction after TGR"
S_LONG = "Not rated: longer than the limit"
NO_NAME = "(not recorded)"
IST_OFFSET = pd.Timedelta(hours=5, minutes=30)


@dataclass
class AssemblySettings:
    std_fixed: float = config.ASM_STD_FIXED            # seconds every order needs whatever its size (0)
    std_per_code: float = config.ASM_STD_PER_CODE      # seconds per different drug code in the order
    max_sec: float = config.ASM_MAX_SEC                # a longer first-subtraction-to-TGR is not rated
    min_show: int = 1               # rated orders a person / the team / a week or month cell needs to show a percentage
    low_sample: int = 15            # cells built on fewer rated orders are shown in grey italics


# ------------------------------------------------------------------ reading the stored values
def clean_text(value):
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    s = str(value).strip()
    return None if s in ("", "NA", "NaN", "None", "nan") else s


def ms_to_ist(ms: pd.Series) -> pd.Series:
    """Epoch milliseconds -> naive IST datetimes cut to the whole second (the same as the extract scripts)."""
    x = pd.to_numeric(ms, errors="coerce")
    return pd.to_datetime((x // 1000) * 1000, unit="ms", errors="coerce") + IST_OFFSET


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Rows from db.ASSEMBLY_QUERY (assembler, n_items, order_id, tgr_ms, first_sub_ms) -> login, codes, order_id, tgr, first_sub."""
    return pd.DataFrame({
        "login": raw["assembler"].map(clean_text),
        "codes": pd.to_numeric(raw["n_items"].map(clean_text), errors="coerce"),
        "order_id": raw["order_id"].map(clean_text),
        "tgr": ms_to_ist(raw["tgr_ms"]),
        "first_sub": ms_to_ist(raw["first_sub_ms"]),
    }).reset_index(drop=True)


def from_extracts(order_csvs, subtractions_csv) -> pd.DataFrame:
    """Dev/testing only: build the same rows from the extract files (past_orders_extracted.csv, cancelled_orders_extracted.csv
    and inventory_subtractions_extracted.csv), whose times are already converted to IST."""
    o = pd.concat([pd.read_csv(p, dtype=str) for p in order_csvs], ignore_index=True)
    o["tgr"] = pd.to_datetime(o["TGR"], errors="coerce")
    o = o[o["tgr"].notna()].sort_values("tgr").drop_duplicates(["phone_number", "order_id"], keep="last")
    sub = pd.read_csv(subtractions_csv, dtype=str, usecols=["phone_number", "order_id", "timestamp"])
    sub["ts"] = pd.to_datetime(sub["timestamp"], errors="coerce")
    first = sub.groupby(["phone_number", "order_id"])["ts"].min().rename("first_sub").reset_index()
    d = o.merge(first, on=["phone_number", "order_id"], how="left")
    return pd.DataFrame({
        "login": d["assembler"].map(clean_text), "codes": pd.to_numeric(d["n_items"], errors="coerce"),
        "order_id": d["order_id"], "tgr": d["tgr"], "first_sub": d["first_sub"],
    }).reset_index(drop=True)


def name_of(login) -> str:
    if not isinstance(login, str) or not login.strip():
        return NO_NAME
    key = login.strip().lower()
    return config.LOGIN_NAMES.get(key) or (key.split("@")[0] if "@" in key else key)


# ------------------------------------------------------------------ rating
def rate_orders(df: pd.DataFrame, s: AssemblySettings) -> pd.DataFrame:
    d = df.copy()
    for c in ["tgr", "first_sub"]:
        d[c] = pd.to_datetime(d[c], errors="coerce")
    d = d[d["tgr"].notna()].copy()
    excluded = {x.lower() for x in config.ASM_EXCLUDED_LOGINS | config.EXCLUDED_LOGINS}
    d = d[~d["login"].fillna("").str.strip().str.lower().isin(excluded)].copy()
    d["name"] = d["login"].map(name_of)
    d["dur"] = (d["tgr"] - d["first_sub"]).dt.total_seconds().round()
    d["std_sec"] = s.std_fixed + s.std_per_code * d["codes"].fillna(0)
    d["status_text"] = np.select(
        [d["name"] == NO_NAME, d["first_sub"].isna(), ~(d["codes"] > 0), d["dur"] < 0, d["dur"] > s.max_sec],
        [S_NO_ASM, S_NO_SUB, S_NO_CODES, S_NEG, S_LONG], default=S_RATED)
    d["rated"] = d["status_text"] == S_RATED
    d["date"] = d["tgr"].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["act_rated"] = np.where(d["rated"], d["dur"], 0.0)
    d["std_rated"] = np.where(d["rated"], d["std_sec"], 0.0)
    d["codes_rated"] = np.where(d["rated"], d["codes"], 0.0)
    return d.reset_index(drop=True)


def person_order(d: pd.DataFrame) -> list[str]:
    names = d.groupby("name").size().sort_values(ascending=False).index.tolist()
    return [n for n in names if n != NO_NAME] + [n for n in names if n == NO_NAME]


def summarize(d: pd.DataFrame, s: AssemblySettings, keys) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    if d.empty and not keys:      # nothing to total: one row of zeros, so the "Team" row can still be shown
        return pd.DataFrame({"orders": [0], "rated": [0], "codes": [0.0], "act_sec": [0.0], "std_sec": [0.0],
                             "avg_sec": [np.nan], "efficiency": [np.nan], "share_rated": [np.nan]})
    g = d.assign(_all=1, _n=1).groupby(keys if keys else ["_all"])
    t = g.agg(orders=("_n", "sum"), rated=("rated", "sum"), codes=("codes_rated", "sum"),
              act_sec=("act_rated", "sum"), std_sec=("std_rated", "sum"))
    ok = (t["rated"] > 0) & (t["act_sec"] > 0)
    t["avg_sec"] = np.where(ok, t["act_sec"] / t["rated"].where(ok, 1), np.nan)
    show = ok & (t["rated"] >= s.min_show) & (t["std_sec"] > 0)
    t["efficiency"] = np.where(show, t["std_sec"] / t["act_sec"].where(ok, 1), np.nan)
    t["share_rated"] = np.where(t["orders"] > 0, t["rated"] / t["orders"].where(t["orders"] > 0, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def overview_table(d: pd.DataFrame, s: AssemblySettings) -> pd.DataFrame:
    t = summarize(d, s, "name").reindex(person_order(d))
    tot = summarize(d, s, []); tot.index = ["Team"]
    return pd.concat([t, tot])


def period_matrices(d: pd.DataFrame, s: AssemblySettings, period: str) -> dict:
    r = d[d["rated"]]
    if r.empty:
        return {k: pd.DataFrame() for k in ["efficiency", "rated", "avg_sec"]}
    names = [n for n in person_order(d) if n in set(r["name"])]
    periods = sorted(r[period].dropna().unique())
    by = summarize(r, s, ["name", period]); by_p = summarize(r, s, [period])
    by_n = summarize(r, s, "name"); tot = summarize(r, s, []).iloc[0]
    out = {}
    for col in ["efficiency", "rated", "avg_sec"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["Team"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


FLAG_COLUMNS = ["name", "order_id", "codes", "first_sub", "tgr", "dur", "std_sec", "review"]


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    """Orders with a wrong or over-long timing, for a supervisor to look at (orders with nothing to time are only counted)."""
    f = d[d["status_text"].isin([S_LONG, S_NEG])].copy()
    if f.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    f["review"] = np.where(f["status_text"] == S_LONG,
                           "Took " + (f["dur"] / 60).map("{:,.0f}".format) + " minutes from the first subtraction to TGR: left open or put on hold.",
                           "The first subtraction is after TGR: a wrong stamp.")
    return f.sort_values("tgr")[FLAG_COLUMNS]


def status_counts(d: pd.DataFrame) -> pd.DataFrame:
    return d.groupby("status_text").size().sort_values(ascending=False).rename("Orders").to_frame()


def data_checks(d: pd.DataFrame) -> dict:
    r = d[d["rated"]]
    return {
        "Orders read (TGR stamped)": int(len(d)),
        "Rated": int(d["rated"].sum()),
        "No inventory subtraction found": int((d["status_text"] == S_NO_SUB).sum()),
        "First subtraction after TGR": int((d["status_text"] == S_NEG).sum()),
        "Longer than the limit": int((d["status_text"] == S_LONG).sum()),
        "No assembler recorded": int((d["status_text"] == S_NO_ASM).sum()),
        "No drug code count": int((d["status_text"] == S_NO_CODES).sum()),
        "Average drug codes per rated order": "-" if r.empty else f"{r['codes'].mean():.1f}",
        "Average seconds per drug code (rated)": "-" if r.empty or r["codes"].sum() == 0 else f"{r['dur'].sum() / r['codes'].sum():.1f}",
        "Data from": str(d["date"].min()), "Data to": str(d["date"].max()),
    }
