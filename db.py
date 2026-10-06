"""Read the make-list lines from the live PostgreSQL database.

Credentials come from Streamlit secrets (see .streamlit/secrets.toml.example) - never
from the code. The query is read-only.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
import pandas as pd
import streamlit as st

QUERY = """
SELECT
    drug_code,
    quantity,
    picker,
    to_timestamp(NULLIF(make_list_created_at, 'NA')::bigint / 1000) AT TIME ZONE 'Asia/Kolkata' AS make_list_created_at,
    to_timestamp(NULLIF(created_at, 'NA')::bigint / 1000)           AT TIME ZONE 'Asia/Kolkata' AS created_at,
    to_timestamp(NULLIF(updated_at, 'NA')::bigint / 1000)           AT TIME ZONE 'Asia/Kolkata' AS updated_at,
    to_timestamp(NULLIF(subtraction_time, 'NA')::bigint)            AT TIME ZONE 'Asia/Kolkata' AS subtraction_time
FROM "master_areaB_way_status"
WHERE NULLIF(make_list_created_at, 'NA')::bigint >= %(start_ms)s
  AND NULLIF(make_list_created_at, 'NA')::bigint <  %(end_ms)s;
"""

IST = timezone(timedelta(hours=5, minutes=30))
PAD_SECONDS = 60   # fetch a little beyond the window so a make list on the edge is not cut in half


def window(start: date, end: date):
    """Inclusive IST dates -> (start_dt, end_dt, start_ms, end_ms).
    start_dt/end_dt are naive IST datetimes (end exclusive) used to keep lists that *start* in the window;
    start_ms/end_ms (epoch milliseconds, padded) go into the SQL WHERE clause."""
    s = datetime.combine(start, time.min, tzinfo=IST)
    e = datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST)
    return (s.replace(tzinfo=None), e.replace(tzinfo=None),
            int(s.timestamp() * 1000) - PAD_SECONDS * 1000,
            int(e.timestamp() * 1000) + PAD_SECONDS * 1000)


def secrets_available() -> bool:
    try:
        return "db" in st.secrets
    except Exception:
        return False


def _run_query(db: dict, start_ms: int, end_ms: int) -> pd.DataFrame:
    import psycopg2
    conn = psycopg2.connect(host=db["host"], port=int(db["port"]), user=db["user"],
                            password=db["password"], dbname=db["name"], connect_timeout=20)
    try:
        cur = conn.cursor()
        cur.execute(QUERY, {"start_ms": int(start_ms), "end_ms": int(end_ms)})
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=900, max_entries=30, show_spinner="Reading the selected dates from the database...")
def fetch_lines(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Only the make lists created inside the window are read. Cached per window for 15 minutes;
    the Refresh button in the app clears the cache."""
    db = dict(st.secrets["db"])
    ssh = dict(st.secrets["ssh"]) if "ssh" in st.secrets and st.secrets["ssh"].get("enabled", True) else None
    if ssh:
        from sshtunnel import SSHTunnelForwarder
        kwargs = dict(ssh_username=ssh["user"], remote_bind_address=(db["host"], int(db["port"])),
                      allow_agent=False, host_pkey_directories=[])
        if ssh.get("private_key"):
            import io, paramiko
            kwargs["ssh_pkey"] = paramiko.RSAKey.from_private_key(io.StringIO(ssh["private_key"]))
        else:
            kwargs["ssh_password"] = ssh["password"]
        with SSHTunnelForwarder((ssh["host"], int(ssh.get("port", 22))), **kwargs) as tunnel:
            local = dict(db, host="127.0.0.1", port=tunnel.local_bind_port)
            df = _run_query(local, start_ms, end_ms)
    else:
        df = _run_query(db, start_ms, end_ms)
    for c in ["make_list_created_at", "created_at", "updated_at", "subtraction_time"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def load_csv(file_or_path) -> pd.DataFrame:
    """Load the CSV written by extract_data.py (or the workbook's source CSV)."""
    df = pd.read_csv(file_or_path)
    for c in ["make_list_created_at", "created_at", "updated_at", "subtraction_time"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df
