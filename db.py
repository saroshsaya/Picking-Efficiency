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


def window(start: date, end: date, pad_seconds: int = PAD_SECONDS):
    """Inclusive IST dates -> (start_dt, end_dt, start_ms, end_ms).
    start_dt/end_dt are naive IST datetimes (end exclusive) used to keep lists that *start* in the window;
    start_ms/end_ms (epoch milliseconds, padded) go into the SQL WHERE clause."""
    s = datetime.combine(start, time.min, tzinfo=IST)
    e = datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST)
    return (s.replace(tzinfo=None), e.replace(tzinfo=None),
            int(s.timestamp() * 1000) - pad_seconds * 1000,
            int(e.timestamp() * 1000) + pad_seconds * 1000)


def secrets_available() -> bool:
    try:
        return "db" in st.secrets
    except Exception:
        return False


PACK_QUERY = """
WITH po AS (
    SELECT id, order_id, phone_number, packer,
           NULLIF("TGR", 'NA')::bigint        AS tgr,
           NULLIF("TGS", 'NA')::bigint        AS tgs,
           NULLIF(label_time, 'NA')::bigint   AS lab,
           NULLIF(check_start, 'NA')::bigint  AS st
    FROM past_orders
    WHERE NULLIF("TGS", 'NA')::bigint >= %(start_ms)s
      AND NULLIF("TGS", 'NA')::bigint <  %(end_ms)s
),
j AS (
    SELECT po.*,
           s."order" AS ord,
           count(s.order_id) OVER (PARTITION BY po.id) AS n_match,
           row_number() OVER (PARTITION BY po.id ORDER BY s.order_id) AS rn
    FROM po
    LEFT JOIN saya_orders s
           ON s.phone_number = po.phone_number AND s.order_id = po.order_id
)
SELECT
    j.id,
    j.packer,
    to_timestamp(j.tgr / 1000) AT TIME ZONE 'Asia/Kolkata' AS "TGR",
    to_timestamp(j.st  / 1000) AT TIME ZONE 'Asia/Kolkata' AS check_start,
    to_timestamp(j.tgs / 1000) AT TIME ZONE 'Asia/Kolkata' AS "TGS",
    to_timestamp(j.lab / 1000) AT TIME ZONE 'Asia/Kolkata' AS label_time,
    CASE WHEN j.n_match = 1 THEN
         (SELECT count(*) FROM jsonb_each(CASE WHEN jsonb_typeof(j.ord) = 'object' THEN j.ord ELSE '{}'::jsonb END))
    END AS distinct_sku_count,
    CASE WHEN j.n_match = 1 THEN
         (SELECT COALESCE(SUM(trunc(
                    CASE WHEN jsonb_typeof(e.value -> 'Quantity') = 'number'
                              THEN (e.value ->> 'Quantity')::numeric
                         WHEN jsonb_typeof(e.value -> 'Quantity') = 'string'
                              AND (e.value ->> 'Quantity') ~ '^[[:space:]]*[0-9]+[[:space:]]*$'
                              THEN (e.value ->> 'Quantity')::numeric
                         ELSE 0 END)), 0)
          FROM jsonb_each(CASE WHEN jsonb_typeof(j.ord) = 'object' THEN j.ord ELSE '{}'::jsonb END) AS e
          WHERE jsonb_typeof(e.value) = 'object')
    END AS total_item_count
FROM j
WHERE j.rn = 1
ORDER BY j.id;
"""
# The join happens inside the database, so customer phone numbers never leave it.
# Orders whose phone number + order_id match more than one saya_orders row get no item counts (NULL).


def _run_query(db: dict, query: str, params: dict) -> pd.DataFrame:
    import psycopg2
    conn = psycopg2.connect(host=db["host"], port=int(db["port"]), user=db["user"],
                            password=db["password"], dbname=db["name"], connect_timeout=20)
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=cols)


def _fetch(query: str, params: dict) -> pd.DataFrame:
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
            return _run_query(dict(db, host="127.0.0.1", port=tunnel.local_bind_port), query, params)
    return _run_query(db, query, params)


@st.cache_data(ttl=900, max_entries=30, show_spinner="Reading the selected dates from the database...")
def fetch_lines(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Picking: only make lists created inside the window are read. Cached per window for 15 minutes."""
    df = _fetch(QUERY, {"start_ms": int(start_ms), "end_ms": int(end_ms)})
    for c in ["make_list_created_at", "created_at", "updated_at", "subtraction_time"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


@st.cache_data(ttl=900, max_entries=30, show_spinner="Reading the selected dates from the database...")
def fetch_packing(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Packing: only orders advanced (TGS) inside the window are read, already joined to their item counts."""
    df = _fetch(PACK_QUERY, {"start_ms": int(start_ms), "end_ms": int(end_ms)})
    for c in ["TGR", "check_start", "TGS", "label_time"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def load_csv(file_or_path) -> pd.DataFrame:
    """Load the CSV written by extract_data.py (or the workbook's source CSV)."""
    df = pd.read_csv(file_or_path)
    for c in ["make_list_created_at", "created_at", "updated_at", "subtraction_time"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df
