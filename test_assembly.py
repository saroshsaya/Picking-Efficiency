import numpy as np, pandas as pd, pytest
import config, assembly
from assembly import AssemblySettings

DEEPAK = "naagard47@gmail.com"
VIJAY = "panindia135@gmail.com"
MOHIT = "mohitpatel639049@gmail.com"      # left out of assembly
B = pd.Timestamp("2026-10-05 10:00:00")   # a Monday


def mk(rows):
    """rows: [login, codes, seconds from first subtraction to TGR (None = no subtraction), TGR offset in days]"""
    out = []
    for i, (login, codes, secs, day) in enumerate(rows):
        tgr = B + pd.Timedelta(days=day, minutes=i)
        out.append({"login": login, "codes": codes, "order_id": str(i + 1), "tgr": tgr,
                    "first_sub": pd.NaT if secs is None else tgr - pd.Timedelta(seconds=secs)})
    return pd.DataFrame(out)


def S(**kw):
    return AssemblySettings(**kw)


def ms(ts):
    return int(pd.Timestamp(ts).tz_localize("Asia/Kolkata").timestamp() * 1000)


# ------------------------------------------------------------------ reading
def test_prepare_converts_ms_to_ist_seconds_and_cleans_text():
    raw = pd.DataFrame({
        "assembler": [DEEPAK, " ", None], "n_items": ["3", "NA", None], "order_id": ["11", "12", "13"],
        "tgr_ms": [ms("2026-10-05 10:00:30") + 999, ms("2026-10-05 11:00:00"), None],
        "first_sub_ms": [ms("2026-10-05 10:00:00") + 1, None, None],
    })
    d = assembly.prepare(raw)
    assert d["tgr"].iloc[0] == pd.Timestamp("2026-10-05 10:00:30") and d["first_sub"].iloc[0] == pd.Timestamp("2026-10-05 10:00:00")
    assert d["first_sub"].iloc[1:].isna().all() and pd.isna(d["tgr"].iloc[2])
    assert d["login"].iloc[1:].isna().all() and d["codes"].iloc[0] == 3 and d["codes"].iloc[1:].isna().all()


def test_from_extracts_takes_the_earliest_subtraction_and_one_row_per_order(tmp_path):
    p, c, s = tmp_path / "p.csv", tmp_path / "c.csv", tmp_path / "s.csv"
    p.write_text("phone_number,order_id,TGR,assembler,n_items,invoice_print_time\n"
                 "+911,5,2026-10-05 10:01:00,a@x.com,2,\n+912,5,2026-10-05 10:05:00,a@x.com,1,\n+913,7,,a@x.com,1,\n")
    c.write_text("phone_number,order_id,TGR,assembler,n_items,invoice_print_time\n+911,5,2026-10-05 10:02:00,a@x.com,2,\n")
    s.write_text("phone_number,order_id,assembler,quantity,timestamp\n"
                 "+911,5,a@x.com,1,2026-10-05 10:00:40\n+911,5,a@x.com,4,2026-10-05 10:00:10\n+919,5,a@x.com,1,2026-10-05 09:00:00\n")
    d = assembly.from_extracts([p, c], s)
    assert len(d) == 2                                           # no-TGR order dropped; the order in both files appears once
    a = d[d["first_sub"].notna()].iloc[0]
    assert a["first_sub"] == pd.Timestamp("2026-10-05 10:00:10") and a["tgr"] == pd.Timestamp("2026-10-05 10:02:00")
    assert d["first_sub"].isna().sum() == 1                      # same order id, another phone number: not linked


# ------------------------------------------------------------------ rating
def test_statuses_in_order_and_limit_is_inclusive():
    d = assembly.rate_orders(mk([
        [DEEPAK, 4, 30, 0], [None, 4, 30, 0], [DEEPAK, 4, None, 0], [DEEPAK, None, 30, 0], [DEEPAK, 0, 30, 0],
        [DEEPAK, 4, -3, 0], [DEEPAK, 4, 901, 0], [DEEPAK, 4, 900, 0], [DEEPAK, 4, 0, 0], [None, 4, None, 0],
    ]), S())
    assert d["status_text"].tolist() == [assembly.S_RATED, assembly.S_NO_ASM, assembly.S_NO_SUB, assembly.S_NO_CODES, assembly.S_NO_CODES,
                                         assembly.S_NEG, assembly.S_LONG, assembly.S_RATED, assembly.S_RATED, assembly.S_NO_ASM]


def test_standard_is_seconds_per_drug_code_from_config():
    assert (config.ASM_STD_PER_CODE, config.ASM_STD_FIXED, config.ASM_MAX_SEC) == (7.7, 0.0, 900)
    d = assembly.rate_orders(mk([[DEEPAK, 10, 77, 0]]), S())
    assert d["std_sec"].iloc[0] == pytest.approx(77.0)
    assert assembly.summarize(d, S(), "name")["efficiency"].iloc[0] == pytest.approx(1.0)


def test_excluded_logins_are_removed_whatever_the_letter_case():
    d = assembly.rate_orders(mk([[DEEPAK, 2, 20, 0], [MOHIT, 2, 20, 0], [" " + MOHIT.upper() + " ", 2, 20, 0], ["deepak86650@gmail.com", 2, 20, 0]]), S())
    assert d["name"].tolist() == ["Deepak"]


def test_efficiency_is_pooled_and_unrated_orders_do_not_count():
    d = assembly.rate_orders(mk([[DEEPAK, 10, 100, 0], [DEEPAK, 1, 54, 0], [DEEPAK, 5, 5000, 0], [VIJAY, 2, 8, 0]]), S())
    t = assembly.overview_table(d, S())
    assert t.loc["Deepak", ["orders", "rated", "codes"]].tolist() == [3, 2, 11]
    assert t.loc["Deepak", "efficiency"] == pytest.approx(11 * 7.7 / 154)          # not the average of 77% and 14%
    assert t.loc["Team", "efficiency"] == pytest.approx(13 * 7.7 / (154 + 8))
    assert t.loc["Team", "share_rated"] == pytest.approx(3 / 4)


def test_changing_the_limit_or_the_standard_changes_the_result():
    rows = mk([[DEEPAK, 5, 60, 0], [DEEPAK, 5, 1200, 0]])
    assert assembly.rate_orders(rows, S())["rated"].tolist() == [True, False]
    assert assembly.rate_orders(rows, S(max_sec=3600))["rated"].tolist() == [True, True]
    e = assembly.summarize(assembly.rate_orders(rows, S(std_per_code=12.0)), S(std_per_code=12.0), "name")["efficiency"].iloc[0]
    assert e == pytest.approx(1.0)


def test_min_show_hides_small_samples():
    d = assembly.rate_orders(mk([[DEEPAK, 5, 60, 0]]), S())
    assert np.isnan(assembly.summarize(d, S(min_show=2), "name")["efficiency"].iloc[0])


def test_people_ordered_by_orders_with_no_name_last():
    d = assembly.rate_orders(mk([[None, 1, 5, 0]] * 3 + [[VIJAY, 1, 5, 0]] * 2 + [[DEEPAK, 1, 5, 0]]), S())
    assert assembly.person_order(d) == ["Vijay", "Deepak", assembly.NO_NAME]


def test_week_starts_on_monday_and_period_tables_add_up():
    d = assembly.rate_orders(mk([[DEEPAK, 2, 20, 0], [DEEPAK, 2, 10, 6], [DEEPAK, 2, 40, 7], [VIJAY, 10, 77, 30]]), S())
    assert d["week"].dt.strftime("%Y-%m-%d").tolist() == ["2026-10-05", "2026-10-05", "2026-10-12", "2026-11-02"]
    w = assembly.period_matrices(d, S(), "week")
    assert w["rated"].loc["Deepak"].tolist()[:2] == [2, 1] and w["rated"].loc["Team", "Overall"] == 4
    assert w["efficiency"].loc["Deepak"].iloc[0] == pytest.approx(4 * 7.7 / 30)
    m = assembly.period_matrices(d, S(), "month")
    assert m["efficiency"].loc["Vijay"].dropna().iloc[0] == pytest.approx(1.0) and [pd.Timestamp(c).strftime("%Y-%m") for c in m["rated"].columns[:2]] == ["2026-10", "2026-11"]


def test_flags_list_only_long_and_wrong_stamp_orders():
    d = assembly.rate_orders(mk([[DEEPAK, 2, 20, 0], [DEEPAK, 2, 2000, 0], [DEEPAK, 2, -4, 0], [DEEPAK, 2, None, 0]]), S())
    f = assembly.flags_table(d)
    assert list(f.columns) == assembly.FLAG_COLUMNS and len(f) == 2
    assert f["review"].str.contains("33 minutes").sum() == 1 and f["review"].str.contains("after TGR").sum() == 1
    assert assembly.status_counts(d)["Orders"].sum() == 4


def test_empty_selection_does_not_break():
    d = assembly.rate_orders(mk([[DEEPAK, 2, 20, 0]]), S()).iloc[0:0]
    t = assembly.overview_table(d, S())
    assert t.loc["Team", "orders"] == 0 and np.isnan(t.loc["Team", "efficiency"])
    assert assembly.period_matrices(d, S(), "week")["efficiency"].empty and assembly.flags_table(d).empty
