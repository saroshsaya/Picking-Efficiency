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
    disp = e.apply(lambda col: col.map(lambda v: "-" if pd.isna(v) else f"{v:.1%}"))
    css = f(e)
    return disp.style.apply(lambda df: css.reindex(index=df.index, columns=df.columns), axis=None)


def fmt_cols(df, **kw):
    return df.style.format(kw, na_rep="-")


# ------------------------------------------------------------------ admin tools (off by default; switched on from the Home page)
def is_admin() -> bool:
    return bool(st.session_state.get("admin_on", False))


def settings_area(col):
    """Where a page puts its settings. Admin: the Settings button. Everyone else: the same inputs, kept out of sight,
    so every page runs on the standards in config.py."""
    if is_admin():
        return col.popover("☰ Settings", use_container_width=True)
    box = st.container(key="hidden_settings")
    box.markdown("<style>.st-key-hidden_settings{display:none !important;}</style>", unsafe_allow_html=True)
    return box


def data_checks_box(checks: dict):
    with st.expander("Data checks (admin)"):
        st.table(pd.DataFrame({"Check": list(checks.keys()), "Value": [f"{v:,}" if isinstance(v, int) else v for v in checks.values()]}))


# ------------------------------------------------------------------ the activity bar at the top fills the window width
NAV_CSS = """
<style>
@media (min-width: 900px) {
  header[data-testid="stHeader"] [data-testid="stToolbar"] > div:first-child,
  header[data-testid="stHeader"] .rc-overflow { flex: 1 1 auto !important; width: 100% !important; }
  header[data-testid="stHeader"] .rc-overflow { display: flex !important; }
  header[data-testid="stHeader"] .rc-overflow-item { flex: 1 1 0 !important; display: flex !important; justify-content: center; }
}
</style>
"""
