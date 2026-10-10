"""Rule tests on tiny synthetic data (no real data, no database)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pandas as pd
import efficiency as eff
from efficiency import Settings

T0 = pd.Timestamp("2026-09-07 09:00:00")   # a Monday


def make_lines(picker, start, n_lines, picked, qty, first_after_min, last_after_min, drug0=1, jitter_ms=0):
    """n_lines lines created at `start`; the first `picked` lines have scan times
    spread between first_after_min and last_after_min."""
    rows = []
    for i in range(n_lines):
        scan = pd.NaT
        if i < picked:
            frac = 0 if picked == 1 else i / (picked - 1)
            scan = start + pd.Timedelta(minutes=first_after_min + frac * (last_after_min - first_after_min))
        rows.append(dict(drug_code=drug0 + i, quantity=qty if i < picked else 0, picker=picker,
                         make_list_created_at=start + pd.Timedelta(milliseconds=jitter_ms * (i % 5)),
                         updated_at=scan))
    return pd.DataFrame(rows)


def run(df, **kw):
    kw.setdefault('model', 1)          # the original pieces-only tests; model 2 is tested below
    s = Settings(**kw)
    lists, _ = eff.build_lists(df, s.merge_seconds)
    return eff.rate_lists(lists, s), s


def test_lines_within_two_seconds_are_one_list():
    df = make_lines("a@x.com", T0, 10, 10, 10, 1, 10, jitter_ms=500)
    r, _ = run(df)
    assert len(r) == 1 and r.loc[0, "lines"] == 10


def test_far_apart_lists_stay_separate():
    df = pd.concat([make_lines("a@x.com", T0, 5, 5, 20, 1, 10),
                    make_lines("a@x.com", T0 + pd.Timedelta(minutes=30), 5, 5, 20, 1, 10, drug0=100)])
    r, _ = run(df)
    assert len(r) == 2


def test_normal_list_timed_from_creation_and_efficiency():
    # 100 pieces, last scan 8 min after creation -> 8 min; benchmark 100*4.78/60 = 7.97 min
    r, s = run(make_lines("a@x.com", T0, 10, 10, 10, 1, 8))
    row = r.iloc[0]
    assert row["status"] == "Rated" and row["minutes"] == 8
    summ = eff.summarize(r, s, [])
    assert abs(summ["efficiency"].iloc[0] - (100 * 4.78 / 60) / 8) < 1e-9


def test_long_wait_is_flagged_and_timed_from_first_scan():
    # first scan 30 min after creation, last at 44 -> 14 min + 1 min allowance
    r, _ = run(make_lines("a@x.com", T0, 10, 10, 20, 30, 44))
    row = r.iloc[0]
    assert row["long_wait"] and row["minutes"] == 15 and row["status"] == "Rated"


def test_lead_in_exactly_at_limit_is_not_long_wait():
    r, _ = run(make_lines("a@x.com", T0, 10, 10, 10, 10, 20))
    assert not r.iloc[0]["long_wait"]


def test_partial_list_is_flagged_and_timed_from_first_scan():
    # 4 of 10 lines picked (40%), 20 pieces each = 80 pieces
    r, _ = run(make_lines("a@x.com", T0, 10, 4, 20, 2, 12))
    row = r.iloc[0]
    assert row["partial"] and row["minutes"] == 11 and row["status"] == "Rated"


def test_small_list_not_rated_but_pieces_counted():
    r, s = run(make_lines("a@x.com", T0, 3, 3, 5, 1, 5))        # 15 pieces
    assert r.iloc[0]["status"] == "Not rated: too small"
    summ = eff.summarize(r, s, [])
    assert summ["pieces_all"].iloc[0] == 15 and summ["pieces_rated"].iloc[0] == 0
    assert pd.isna(summ["efficiency"].iloc[0])


def test_too_long_not_rated():
    r, _ = run(make_lines("a@x.com", T0, 10, 10, 20, 1, 120))   # 120 min > 90
    assert r.iloc[0]["status"] == "Not rated: too long" and "Too long" in r.iloc[0]["flags"]


def test_nothing_picked():
    r, _ = run(make_lines("a@x.com", T0, 10, 0, 0, 0, 0))
    assert r.iloc[0]["status"] == "Nothing picked" and not r.iloc[0]["flagged"]


def test_over_max_drug_codes_flagged():
    r, _ = run(make_lines("a@x.com", T0, 60, 60, 5, 1, 20))
    assert r.iloc[0]["over_codes"] and "Over 48" in r.iloc[0]["flags"]


def test_pooled_not_averaged():
    # big slow list (400 pieces / 60 min) + small fast list (80 pieces / 10 min):
    # pooled efficiency differs from the mean of the two per-list efficiencies
    a = make_lines("a@x.com", T0, 20, 20, 20, 1, 60)
    b = make_lines("a@x.com", T0 + pd.Timedelta(hours=2), 20, 20, 4, 1, 10, drug0=500)
    r, s = run(pd.concat([a, b]))
    pooled = eff.summarize(r, s, [])["efficiency"].iloc[0]
    per_list = (r["pieces"] * 4.78 / 60 / r["minutes"]).mean()
    assert abs(pooled - ((480 * 4.78 / 60) / 70)) < 1e-9
    assert abs(pooled - per_list) > 0.01


def test_week_starts_on_monday():
    r, _ = run(make_lines("a@x.com", T0 + pd.Timedelta(days=6), 10, 10, 10, 1, 8))   # a Sunday
    assert r.iloc[0]["week"] == pd.Timestamp("2026-09-07")


def test_min_pieces_show_hides_small_cells():
    r, s = run(make_lines("a@x.com", T0, 10, 10, 10, 1, 8), min_pieces_show=500)
    assert pd.isna(eff.summarize(r, s, [])["efficiency"].iloc[0])


def test_window_converts_ist_dates_to_padded_epoch_ms():
    import datetime as dt
    import db
    s_dt, e_dt, s_ms, e_ms = db.window(dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    assert s_dt == dt.datetime(2026, 9, 1, 0, 0) and e_dt == dt.datetime(2026, 9, 8, 0, 0)       # end exclusive
    # 2026-09-01 00:00 IST = 2026-08-31 18:30 UTC, minus the 60 s pad
    assert s_ms == int(pd.Timestamp("2026-08-31 18:30", tz="UTC").timestamp() * 1000) - 60_000
    assert e_ms == int(pd.Timestamp("2026-09-07 18:30", tz="UTC").timestamp() * 1000) + 60_000


def test_list_on_window_edge_is_whole_when_padding_is_fetched():
    # a list whose lines straddle midnight by a few ms: padded fetch keeps both halves, the list starts inside/outside as a whole
    T = pd.Timestamp("2026-09-08 00:00:00")
    df = make_lines("a@x.com", T - pd.Timedelta(milliseconds=5), 10, 10, 10, 1, 8)
    df.loc[5:, "make_list_created_at"] += pd.Timedelta(milliseconds=12)
    lists, _ = eff.build_lists(df, 2)
    assert len(lists) == 1 and lists.loc[0, "lines"] == 10



def test_two_factor_standard_uses_pieces_and_picked_skus():
    import config
    r, s = run(make_lines("a@x.com", T0, 10, 10, 10, 1, 8), model=2)          # 100 pieces, 10 distinct SKUs
    assert r.loc[0, "picked_distinct"] == 10
    assert abs(r.loc[0, "bench_minutes"] * 60 - config.PICK_LEVEL_TYPICAL * (config.PICK_STD_B_PIECE * 100 + config.PICK_STD_C_SKU * 10)) < 1e-6


def test_level_modes_and_override():
    from efficiency import level_from_study
    typical = Settings(); study = Settings(level_mode="study"); custom = Settings(level=1.0)
    assert typical.k == 0.93 and abs(study.k - level_from_study(study.b_piece, study.c_sku)) < 1e-12 and custom.k == 1.0
    assert study.k < typical.k            # study pace is the stricter standard


def test_partial_list_counts_only_picked_skus():
    r, _ = run(make_lines("a@x.com", T0, 10, 4, 20, 2, 12), model=2)           # 4 of 10 lines picked
    assert r.loc[0, "picked_distinct"] == 4 and r.loc[0, "pieces"] == 80


def test_fewer_pieces_per_sku_is_not_penalised():
    # same 20 SKUs; with the SKU term the standard of the small list stays close to the big one's
    big, s = run(make_lines("a@x.com", T0, 20, 20, 15, 1, 20), model=2, level=1.0)
    small, _ = run(make_lines("a@x.com", T0, 20, 20, 3, 1, 20), model=2, level=1.0)
    two_big, two_small = big.loc[0, "bench_minutes"] * 60, small.loc[0, "bench_minutes"] * 60
    assert two_small / two_big > (60 * 4.78) / (300 * 4.78)            # better than a flat per-piece rate
    assert abs(two_big - (s.b_piece * 300 + s.c_sku * 20)) < 1e-6


def test_fit_shape_recovers_known_coefficients():
    import numpy as np
    rng = np.random.default_rng(1)
    pieces = rng.integers(60, 400, 80); skus = rng.integers(30, 49, 80)
    df = pd.DataFrame({"rated": True, "pieces": pieces, "picked_distinct": skus, "minutes": (2.0 * pieces + 20.0 * skus) / 60})
    b, c, n, r2, k = eff.fit_shape(df, within_person=False)
    assert abs(b - 2.0) < 1e-6 and abs(c - 20.0) < 1e-6 and n == 80 and r2 > 0.999 and abs(k - 1.0) < 1e-9


def test_fit_shape_within_person_ignores_who_takes_the_big_lists():
    import numpy as np
    rng = np.random.default_rng(2)
    rows = []
    for name, base, lo in [("fast", 200.0, 250), ("slow", 900.0, 60)]:       # the fast person takes the big lists
        for _ in range(60):
            p, s_ = int(rng.integers(lo, lo + 150)), int(rng.integers(30, 49))
            rows.append({"exec": name, "rated": True, "pieces": p, "picked_distinct": s_, "minutes": (base + 2.0 * p + 20.0 * s_) / 60})
    df = pd.DataFrame(rows)
    b_w, c_w, *_ = eff.fit_shape(df, within_person=True)
    b_p, c_p, *_ = eff.fit_shape(df, within_person=False)
    assert abs(b_w - 2.0) < 1e-6 and abs(c_w - 20.0) < 1e-6          # within-person recovers the truth
    assert abs(b_p - 2.0) > 0.3                                          # pooled is pulled off by the mix
