"""Shared UI helpers: access gate and the efficiency colouring used by every page."""
import hmac
import numpy as np
import pandas as pd
import streamlit as st


# ------------------------------------------------------------------ access gate
def password_ok() -> bool:
    try:
        pw = st.secrets.get("app_password")
    except Exception:
        pw = None
    if not pw:
        return True
    if st.session_state.get("authed"):
        return True
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered, pw):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False




def _blend(rgb, t):
    return tuple(int(round(255 - (255 - c) * t)) for c in rgb)


def cell_color(v):
    if v < 1:
        r, g, b = _blend((248, 105, 107), min(1.0, (1 - v) / 0.4))
    else:
        r, g, b = _blend((99, 190, 123), min(1.0, (v - 1) / 0.4))
    return f"background-color: rgb({r},{g},{b})"


def style_eff(e: pd.DataFrame, p: pd.DataFrame, low: int):
    def f(df):
        out = pd.DataFrame("", index=df.index, columns=df.columns)
        for c in df.columns:
            for i in df.index:
                v = df.at[i, c]
                if pd.isna(v):
                    continue
                css = cell_color(v)
                pv = p.at[i, c] if (i in p.index and c in p.columns) else np.inf
                if pd.notna(pv) and pv < low:
                    css += "; color: #808080; font-style: italic"
                out.at[i, c] = css
        return out
    return e.style.apply(f, axis=None).format("{:.1%}", na_rep="-")


def fmt_cols(df, **kw):
    return df.style.format(kw, na_rep="-")


