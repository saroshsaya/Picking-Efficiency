"""Move (stock moved from an LT rack to an ST cubicle) efficiency logic. Pure pandas - no Streamlit, no database.

One row per move of one drug code (table past_moves):
    moved_by (login), DC (drug code), cumulative_change (units moved), move_start (MOVE pressed), move_time (SUBMIT pressed)

Rules (same idea as the other activity tabs):
 * duration = move_time - move_start  (MOVE pressed to SUBMIT pressed; the walk to the rack, the box and the cubicle are inside it)
 * the person rated is moved_by
 * standard seconds = fixed seconds + seconds per unit x units moved  (from the move time study, set in config.py or in Settings)
 * a move is RATED when it has a start time, the end is not before the start, the duration is between the shortest and the
   longest time that can be rated, and (when the standard uses units) the units moved are known
 * efficiency = sum(standard seconds) / sum(actual seconds) over rated moves
 * moves with no start time (everything before the start stamp existed) are counted but cannot be timed
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

import config
from dto import epoch_to_ist, clean_text, name_of   # same reading of stored epoch values / names as the DTO tab

S_RATED = "Rated"
S_NO_START = "Not rated: no start time"
S_NEG = "Not rated: submit before start"
S_SHORT = "Not rated: start and submit too close"
S_LONG = "Not rated: too long"
S_NO_QTY = "Not rated: units moved not recorded"


@dataclass
class MoveSettings:
    std_fixed: float = config.MOVE_STD_FIXED          # seconds every move needs whatever its size
    std_per_unit: float = config.MOVE_STD_PER_UNIT    # extra seconds per unit moved
    min_sec: float = config.MOVE_MIN_SEC
    max_sec: float = config.MOVE_MAX_SEC
    slow_x: float = config.MOVE_SLOW_X
    min_show: int = 1               # rated moves a person (or the team) needs on the Overview to show a percentage
    min_show_period: int = 1        # rated moves needed in a week / month cell
    low_sample: int = 15            # cells built on fewer rated moves are shown in grey italics

    @property
    def std_is_set(self) -> bool:
        return (self.std_fixed + self.std_per_unit) > 0


# ------------------------------------------------------------------ reading the stored values
def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Rows as stored (epoch text) -> login, dc, qty, move_start, move_time with IST datetimes."""
    d = pd.DataFrame({
        "login": raw["moved_by"].map(clean_text),
        "dc": raw["DC" if "DC" in raw.columns else "dc"].map(clean_text),
        "qty": pd.to_numeric(raw["cumulative_change"].map(clean_text), errors="coerce"),
        "move_start": pd.to_datetime(raw["move_start"].map(epoch_to_ist), errors="coerce"),
        "move_time": pd.to_datetime(raw["move_time"].map(epoch_to_ist), errors="coerce"),
    })
    return d.reset_index(drop=True)


def load_csv(path) -> pd.DataFrame:
    """Dev/testing only: a CSV with columns moved_by, DC, cumulative_change, move_start, move_time.
    Works with the file written by extract_move.py (times already converted) and with raw epoch values."""
    raw = pd.read_csv(path, dtype=str)
    converted = any(raw[c].str.contains("-", na=False).any() for c in ("move_time", "move_start"))
    if not converted:
        return prepare(raw)
    return pd.DataFrame({
        "login": raw["moved_by"].map(clean_text), "dc": raw["DC"].map(clean_text),
        "qty": pd.to_numeric(raw["cumulative_change"].map(clean_text), errors="coerce"),
        "move_start": pd.to_datetime(raw["move_start"], errors="coerce"),
        "move_time": pd.to_datetime(raw["move_time"], errors="coerce"),
    })


# ------------------------------------------------------------------ rating
def rate_moves(df: pd.DataFrame, s: MoveSettings) -> pd.DataFrame:
    d = df.copy()
    for c in ["move_start", "move_time"]:
        d[c] = pd.to_datetime(d[c], errors="coerce")
    d = d[d["move_time"].notna()].copy()
    excluded = {x.lower() for x in config.MOVE_EXCLUDED_LOGINS}
    d = d[~d["login"].fillna("").str.lower().isin(excluded)].copy()
    d["name"] = d["login"].map(name_of)
    d["dur"] = (d["move_time"] - d["move_start"]).dt.total_seconds().round()
    d["std_sec"] = s.std_fixed + s.std_per_unit * d["qty"].fillna(0)
    no_qty = (d["qty"].isna() & (s.std_per_unit > 0))
    d["status_text"] = np.select(
        [d["move_start"].isna(), d["dur"] < 0, d["dur"] < s.min_sec, d["dur"] > s.max_sec, no_qty],
        [S_NO_START, S_NEG, S_SHORT, S_LONG, S_NO_QTY], default=S_RATED)
    d["rated"] = d["status_text"] == S_RATED
    d["date"] = d["move_time"].dt.normalize()
    d["week"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["act_rated"] = np.where(d["rated"], d["dur"], 0.0)
    d["std_rated"] = np.where(d["rated"], d["std_sec"], 0.0)
    d["slow"] = d["rated"] & (d["std_sec"] > 0) & (d["dur"] > s.slow_x * d["std_sec"])
    return d.reset_index(drop=True)


def person_order(d: pd.DataFrame) -> list[str]:
    return d.groupby("name").size().sort_values(ascending=False).index.tolist()


def summarize(d: pd.DataFrame, s: MoveSettings, keys, min_show: int | None = None) -> pd.DataFrame:
    keys = [keys] if isinstance(keys, str) else list(keys)
    if d.empty and not keys:      # nothing to total: one row of zeros, so the "Team" row can still be shown
        return pd.DataFrame({"moves": [0], "rated": [0], "act_sec": [0.0], "std_sec": [0.0],
                             "avg_sec": [np.nan], "efficiency": [np.nan], "share_rated": [np.nan]})
    g = d.assign(_all=1, _n=1).groupby(keys if keys else ["_all"])
    t = g.agg(moves=("_n", "sum"), rated=("rated", "sum"), act_sec=("act_rated", "sum"), std_sec=("std_rated", "sum"))
    ok = (t["rated"] > 0) & (t["act_sec"] > 0)
    ms = s.min_show if min_show is None else min_show
    t["avg_sec"] = np.where(ok, t["act_sec"] / t["rated"].where(ok, 1), np.nan)
    show = ok & (t["rated"] >= ms) & (t["std_sec"] > 0)        # no standard set -> no percentage
    t["efficiency"] = np.where(show, t["std_sec"] / t["act_sec"].where(ok, 1), np.nan)
    t["share_rated"] = np.where(t["moves"] > 0, t["rated"] / t["moves"].where(t["moves"] > 0, 1), np.nan)
    if not keys:
        t = t.reset_index(drop=True)
    return t


def overview_table(d: pd.DataFrame, s: MoveSettings) -> pd.DataFrame:
    t = summarize(d, s, "name").reindex(person_order(d))
    tot = summarize(d, s, []); tot.index = ["Team"]
    t = pd.concat([t, tot])
    if not s.std_is_set:
        note = "Standard not set"
    else:
        note = np.where(t["rated"] == 0, "No timed moves (the start stamp exists from " + config.MOVE_START_FROM + ")",
                        np.where(t["efficiency"].isna(), "Fewer than " + str(s.min_show) + " rated moves: efficiency not shown.", ""))
    t["note"] = np.where(t["moves"] == 0, "No moves", note)
    return t


def period_matrices(d: pd.DataFrame, s: MoveSettings, period: str) -> dict:
    r = d[d["rated"]]
    if r.empty:
        return {k: pd.DataFrame() for k in ["efficiency", "rated", "avg_sec"]}
    names = [n for n in person_order(d) if n in set(r["name"])]
    periods = sorted(r[period].dropna().unique())
    mp = s.min_show_period
    by = summarize(r, s, ["name", period], min_show=mp); by_p = summarize(r, s, [period], min_show=mp)
    by_n = summarize(r, s, "name", min_show=mp)
    tot = summarize(r, s, [], min_show=mp).iloc[0]
    out = {}
    for col in ["efficiency", "rated", "avg_sec"]:
        m = by[col].unstack(period).reindex(index=names, columns=periods)
        m["Overall"] = by_n[col].reindex(names)
        m.loc["Team"] = by_p[col].reindex(periods).tolist() + [tot[col]]
        out[col] = m
    return out


FLAG_COLUMNS = ["name", "dc", "qty", "move_start", "move_time", "dur", "std_sec", "review"]


def flags_table(d: pd.DataFrame) -> pd.DataFrame:
    f = d[d["slow"] | d["status_text"].isin([S_LONG, S_NEG])].copy()
    if f.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    f["review"] = np.select(
        [f["status_text"] == S_LONG, f["status_text"] == S_NEG],
        ["Took " + (f["dur"] / 60).map("{:,.0f}".format) + " minutes: a break, or the start was left open.",
         "Submit is before start."],
        default="Slow: " + (f["dur"] / 60).map("{:.1f}".format) + " minutes.")
    return f.sort_values("move_time")[FLAG_COLUMNS]


def status_counts(d: pd.DataFrame) -> pd.DataFrame:
    return d.groupby("status_text").size().sort_values(ascending=False).rename("Moves").to_frame()


def data_checks(d: pd.DataFrame) -> dict:
    timed = d["move_start"].notna()
    dur = d.loc[timed, "dur"]
    return {
        "Moves read": int(len(d)),
        "With a start time": int(timed.sum()),
        "... start and submit at exactly the same time": int((timed & (d["dur"] == 0)).sum()),
        "... timed moves under 10 seconds": int((dur.between(0, 9)).sum()),
        "Median seconds of timed moves": "-" if dur.empty else f"{dur.median():,.0f}",
        "Submit before start": int((d["status_text"] == S_NEG).sum()),
        "Moves with no units recorded": int(d["qty"].isna().sum()),
        "Moves with 0 units": int((d["qty"] == 0).sum()),
        "Data from": str(d["date"].min()), "Data to": str(d["date"].max()),
    }
