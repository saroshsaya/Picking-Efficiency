import numpy as np, pandas as pd, pytest
import config, dto
from dto import DtoSettings

AKASH = "akashk00525@gmail.com"
SUNNY = "sunnyrajak618@gmail.com"


def ep_s(ts):
    """IST wall-clock text -> 10-digit epoch seconds (newer module)."""
    return str(int(pd.Timestamp(ts).tz_localize("Asia/Kolkata").timestamp()))


def ep_ms(ts):
    """IST wall-clock text -> 13-digit epoch milliseconds (older module)."""
    return ep_s(ts) + "000"


def mk(rows):
    """rows: [awb, login, accepted_at, spt, trh] as datetimes/None, already prepared."""
    d = pd.DataFrame(rows, columns=["awb", "accepted_by", "accepted_at", "spt", "trh"])
    for c in ("accepted_at", "spt", "trh"):
        d[c] = pd.to_datetime(d[c])
    return d


def t(base, secs):
    return pd.Timestamp(base) + pd.Timedelta(seconds=secs)


# ------------------------------------------------------------------ reading stored values
def test_epoch_units_seconds_and_milliseconds():
    assert dto.epoch_to_ist(ep_s("2026-10-20 14:03:09")) == pd.Timestamp("2026-10-20 14:03:09")
    assert dto.epoch_to_ist(ep_ms("2026-10-20 14:03:09")) == pd.Timestamp("2026-10-20 14:03:09")
    assert dto.epoch_to_ist(ep_s("2026-10-20 14:03:09") + ".0") == pd.Timestamp("2026-10-20 14:03:09")


@pytest.mark.parametrize("v", [None, "", "NA", "NaN", "nan", "abc", "123", "12345678901234", float("nan")])
def test_epoch_bad_values_are_none(v):
    assert dto.epoch_to_ist(v) is None


def test_prepare_mixed_units_and_text_cleaning():
    raw = pd.DataFrame({
        "awb": ["A1", " A2 ", "NA", None],
        "accepted_by": [AKASH, SUNNY, "", "NaN"],
        "accepted_at": [ep_ms("2026-10-20 10:00:00"), ep_s("2026-10-21 10:00:00"), "NA", None],
        "spt": [ep_ms("2026-10-20 10:01:00"), ep_s("2026-10-21 10:01:00"), "NA", None],
        "trh": [ep_ms("2026-10-20 10:03:27"), ep_s("2026-10-21 10:03:27"), "NaN", None],
    })
    d = dto.prepare(raw)
    assert list(d["awb"].iloc[:2]) == ["A1", "A2"] and d["awb"].iloc[2:].isna().all()
    assert list(d["accepted_by"].iloc[:2]) == [AKASH, SUNNY] and d["accepted_by"].iloc[2:].isna().all()
    # both units give the same to-the-second answer: 147 seconds
    assert (d["trh"] - d["spt"]).dt.total_seconds().iloc[:2].tolist() == [147, 147]
    assert d["spt"].iloc[2:].isna().all()


# ------------------------------------------------------------------ names
def test_name_of():
    assert dto.name_of(AKASH) == "Akash"
    assert dto.name_of(" " + SUNNY.upper() + " ") == "Sunny"
    assert dto.name_of(None) == "(not accepted)"
    assert dto.name_of("  ") == "(not accepted)"
    assert dto.name_of("someone.new@example.com") == "someone.new"
    for login, name in config.DTO_LOGIN_NAMES.items():
        assert dto.name_of(login) == name


# ------------------------------------------------------------------ rules and status
def test_statuses_and_rated():
    s = DtoSettings(std_sec=147)
    b = "2026-10-20 10:00:00"
    d = dto.rate_dtos(mk([
        ["a1", AKASH, b, t(b, 5), t(b, 152)],       # 147 s: rated
        ["a2", AKASH, b, t(b, 5), None],             # not ended
        ["a3", AKASH, b, None, t(b, 100)],           # no start
        ["a4", AKASH, b, t(b, 200), t(b, 100)],      # end before start
        ["a5", AKASH, b, t(b, 5), t(b, 5)],          # same moment
        ["a6", AKASH, b, t(b, 5), t(b, 5 + 3600)],   # too long
        ["a7", AKASH, b, t(b, 0), t(b, 10)],         # exactly the minimum: rated
        ["a8", AKASH, b, t(b, 0), t(b, 1800)],       # exactly the maximum: rated
    ]), s)
    assert list(d["status_text"]) == [dto.S_RATED, dto.S_NO_TIME, dto.S_NO_TIME, dto.S_NEG, dto.S_SAME, dto.S_LONG,
                                      dto.S_RATED, dto.S_RATED]
    assert list(d["rated"]) == [True, False, False, False, False, False, True, True]
    assert list(d["finished"]) == [True, False, True, True, True, True, True, True]
    assert d.loc[0, "std_sec"] == 147


def test_allowance_scales_standard():
    d = dto.rate_dtos(mk([["a", AKASH, "2026-10-20 10:00", "2026-10-20 10:00:00", "2026-10-20 10:03:00"]]),
                      DtoSettings(std_sec=100, allowance=0.10))
    assert d.loc[0, "std_sec"] == pytest.approx(110)


def test_date_week_month_use_end_day_then_fall_back():
    d = dto.rate_dtos(mk([
        ["a", AKASH, "2026-10-19 09:00", "2026-10-31 23:58:00", "2026-11-01 00:01:00"],   # ends in November, a Sunday
        ["b", AKASH, "2026-10-21 09:00", "2026-10-21 09:30:00", None],                    # not ended: start day
        ["c", AKASH, "2026-10-22 09:00", None, None],                                     # only accepted
    ]), DtoSettings())
    assert d.loc[0, "date"] == pd.Timestamp("2026-11-01")
    assert d.loc[0, "week"] == pd.Timestamp("2026-10-26")      # Monday of that week
    assert d.loc[0, "month"] == pd.Timestamp("2026-11-01")
    assert d.loc[1, "date"] == pd.Timestamp("2026-10-21")
    assert d.loc[2, "date"] == pd.Timestamp("2026-10-22")


def test_slow_flag_is_rated_only_and_above_multiple():
    b = "2026-10-20 10:00:00"
    d = dto.rate_dtos(mk([
        ["a", AKASH, b, b, t(b, 441)],      # exactly 3 x 147: not slow
        ["b", AKASH, b, b, t(b, 442)],      # just above: slow
        ["c", AKASH, b, b, t(b, 4000)],     # too long: not rated, so not "slow"
    ]), DtoSettings(std_sec=147, slow_x=3))
    assert list(d["slow"]) == [False, True, False]


# ------------------------------------------------------------------ efficiency
def test_pooled_efficiency_not_average_of_percentages():
    b = "2026-10-20 10:00:00"
    s = DtoSettings(std_sec=100, min_show=0)
    d = dto.rate_dtos(mk([["a", AKASH, b, b, t(b, 50)],       # 200 %
                          ["b", AKASH, b, b, t(b, 400)]]), s)  # 25 %
    ov = dto.overview_table(d, s)
    # pooled: 200 standard seconds / 450 actual seconds = 44.4 %  (not the 112.5 % average of 200 % and 25 %)
    assert ov.loc["Akash", "efficiency"] == pytest.approx(200 / 450)
    assert ov.loc["Akash", "avg_sec"] == pytest.approx(225)
    assert ov.loc["Team", "efficiency"] == pytest.approx(200 / 450)


def test_team_is_100_when_standard_equals_average():
    b = "2026-10-20 10:00:00"
    secs = [100, 147, 194]                       # average 147
    d = dto.rate_dtos(mk([[f"a{i}", AKASH if i % 2 else SUNNY, b, b, t(b, x)] for i, x in enumerate(secs)]),
                      DtoSettings(std_sec=147, min_show=0))
    ov = dto.overview_table(d, DtoSettings(std_sec=147, min_show=0))
    assert ov.loc["Team", "efficiency"] == pytest.approx(1.0)


def test_not_rated_dtos_do_not_count_in_efficiency_but_are_finished():
    b = "2026-10-20 10:00:00"
    s = DtoSettings(std_sec=147, min_show=0)
    d = dto.rate_dtos(mk([["a", AKASH, b, b, t(b, 147)],
                          ["b", AKASH, b, b, t(b, 0)],            # same moment
                          ["c", AKASH, b, b, t(b, 7200)],         # too long
                          ["d", AKASH, b, b, None]]), s)          # not finished
    ov = dto.overview_table(d, s)
    assert ov.loc["Akash", "finished"] == 3
    assert ov.loc["Akash", "rated"] == 1
    assert ov.loc["Akash", "share_rated"] == pytest.approx(1 / 3)
    assert ov.loc["Akash", "efficiency"] == pytest.approx(1.0)


def test_min_show_gives_dash_and_note():
    b = "2026-10-20 10:00:00"
    d = dto.rate_dtos(mk([[f"a{i}", AKASH, b, b, t(b, 120)] for i in range(19)]), DtoSettings())
    ov = dto.overview_table(d, DtoSettings(min_show=20))
    assert np.isnan(ov.loc["Akash", "efficiency"]) and np.isnan(ov.loc["Team", "efficiency"])
    assert ov.loc["Akash", "avg_sec"] == pytest.approx(120)        # the average is still shown
    assert "Fewer than 20" in ov.loc["Akash", "note"]
    d2 = dto.rate_dtos(mk([[f"a{i}", AKASH, b, b, t(b, 120)] for i in range(20)]), DtoSettings())
    ov2 = dto.overview_table(d2, DtoSettings(min_show=20))
    assert ov2.loc["Akash", "efficiency"] == pytest.approx(147 / 120)
    assert ov2.loc["Akash", "note"] == ""


def test_overview_with_nothing_finished_does_not_crash():
    b = "2026-10-20 10:00:00"
    s = DtoSettings()
    for rows in ([], [["a", AKASH, b, b, None]]):                       # no DTOs at all / only an unfinished one
        d = dto.rate_dtos(mk(rows), s)
        ov = dto.overview_table(d, s)
        assert list(ov.index) == ["Team"]
        assert ov.loc["Team", "finished"] == 0 and np.isnan(ov.loc["Team", "efficiency"])
        assert ov.loc["Team", "note"] == "No finished DTOs"


def test_overview_orders_by_finished_and_ends_with_team():
    b = "2026-10-20 10:00:00"
    rows = [[f"s{i}", SUNNY, b, b, t(b, 100)] for i in range(3)] + [[f"k{i}", AKASH, b, b, t(b, 100)] for i in range(5)]
    d = dto.rate_dtos(mk(rows), DtoSettings())
    assert list(dto.overview_table(d, DtoSettings()).index) == ["Akash", "Sunny", "Team"]


# ------------------------------------------------------------------ weekly / monthly
def test_period_matrices_shape_and_values():
    s = DtoSettings(std_sec=100, min_show_period=0)
    d = dto.rate_dtos(mk([
        ["a", AKASH, "2026-10-12 10:00", "2026-10-12 10:00:00", "2026-10-12 10:02:00"],    # week of 12 Oct, 120 s
        ["b", AKASH, "2026-10-19 10:00", "2026-10-19 10:00:00", "2026-10-19 10:01:00"],    # week of 19 Oct, 60 s
        ["c", SUNNY, "2026-10-19 10:00", "2026-10-19 10:00:00", "2026-10-19 10:03:00"],    # week of 19 Oct, 180 s
    ]), s)
    m = dto.period_matrices(d, s, "week")
    e = m["efficiency"]
    assert e.shape == (3, 3)                                    # Akash, Sunny, Team  x  2 weeks + Overall
    assert e.loc["Akash", pd.Timestamp("2026-10-12")] == pytest.approx(100 / 120)
    assert e.loc["Akash", pd.Timestamp("2026-10-19")] == pytest.approx(100 / 60)
    assert np.isnan(e.loc["Sunny", pd.Timestamp("2026-10-12")])
    assert e.loc["Team", pd.Timestamp("2026-10-19")] == pytest.approx(200 / 240)
    assert e.loc["Team", "Overall"] == pytest.approx(300 / 360)
    assert m["rated"].loc["Team", "Overall"] == 3


def test_period_min_show_period_dash():
    s = DtoSettings(std_sec=100, min_show_period=2)
    d = dto.rate_dtos(mk([["a", AKASH, "2026-10-12 10:00", "2026-10-12 10:00:00", "2026-10-12 10:02:00"]]), s)
    e = dto.period_matrices(d, s, "month")["efficiency"]
    assert e.isna().all().all()


def test_period_matrices_empty_when_nothing_rated():
    d = dto.rate_dtos(mk([["a", AKASH, "2026-10-12 10:00", "2026-10-12 10:00:00", None]]), DtoSettings())
    m = dto.period_matrices(d, DtoSettings(), "week")
    assert all(v.empty for v in m.values())


# ------------------------------------------------------------------ flags and checks
def test_flags_table_lists_slow_long_and_negative_with_text():
    b = "2026-10-20 10:00:00"
    s = DtoSettings(std_sec=147, slow_x=3)
    d = dto.rate_dtos(mk([
        ["ok", AKASH, b, b, t(b, 147)],
        ["slow", AKASH, b, t(b, 10), t(b, 10 + 600)],
        ["long", SUNNY, b, t(b, 20), t(b, 20 + 3600)],
        ["neg", SUNNY, b, t(b, 500), t(b, 400)],
        ["same", SUNNY, b, t(b, 30), t(b, 30)],
    ]), s)
    f = dto.flags_table(d)
    assert list(f["awb"]) == ["slow", "long", "neg"] or sorted(f["awb"]) == ["long", "neg", "slow"]
    r = f.set_index("awb")["review"]
    assert r["slow"] == "Slow: 10.0 minutes."
    assert r["long"] == "Took 60 minutes: the start or the end may have been missed."
    assert r["neg"] == "End is before start."
    assert "ok" not in set(f["awb"]) and "same" not in set(f["awb"])


def test_flags_table_empty_has_columns():
    d = dto.rate_dtos(mk([["a", AKASH, "2026-10-20 10:00", "2026-10-20 10:00:00", "2026-10-20 10:02:27"]]), DtoSettings())
    f = dto.flags_table(d)
    assert f.empty and list(f.columns) == ["name", "awb", "spt", "trh", "dur", "std_sec", "review"]


def test_status_counts():
    b = "2026-10-20 10:00:00"
    d = dto.rate_dtos(mk([["a", AKASH, b, b, t(b, 100)], ["b", AKASH, b, b, t(b, 120)], ["c", AKASH, b, b, None]]), DtoSettings())
    c = dto.status_counts(d)["DTOs"]
    assert c[dto.S_RATED] == 2 and c[dto.S_NO_TIME] == 1


def test_data_checks():
    b = "2026-10-20 10:00:00"
    d = dto.rate_dtos(mk([
        ["a", AKASH, b, pd.Timestamp(b), t(b, 100)],        # start equals Accept Return
        ["a", AKASH, b, t(b, 5), t(b, 5)],                   # repeated AWB; start and end identical
        [None, AKASH, b, t(b, 5), pd.Timestamp(b) + pd.Timedelta(seconds=-5)],   # no AWB; end before start
        ["d", AKASH, b, t(b, 5), None],
    ]), DtoSettings())
    c = dto.data_checks(d)
    assert c["DTOs read"] == 4
    assert c["With both a start and an end"] == 3
    assert c["... start and end at exactly the same recorded time"] == 1
    assert c["... start equals the Accept Return time"] == 1
    assert c["End before start"] == 1
    assert c["AWB repeated"] == 1
    assert c["DTOs with no AWB"] == 1


def test_all_in_one_row_of_real_style_data_round_trip():
    """Raw epoch text of both modules -> prepare -> rate: both give a rated 147 s DTO."""
    raw = pd.DataFrame({
        "awb": ["X1", "X2"], "accepted_by": [AKASH, SUNNY],
        "accepted_at": [ep_ms("2026-10-20 10:00:00"), ep_s("2026-10-20 10:00:00")],
        "spt": [ep_ms("2026-10-20 10:01:00"), ep_s("2026-10-20 10:01:00")],
        "trh": [ep_ms("2026-10-20 10:03:27"), ep_s("2026-10-20 10:03:27")],
    })
    d = dto.rate_dtos(dto.prepare(raw), DtoSettings())
    assert list(d["dur"]) == [147, 147] and d["rated"].all()
    assert list(d["name"]) == ["Akash", "Sunny"]
