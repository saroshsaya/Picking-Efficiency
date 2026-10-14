"""Read the make-list lines from the live PostgreSQL database.

Credentials come from Streamlit secrets (see .streamlit/secrets.toml.example) - never
from the code. The query is read-only.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
import numpy as np
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


CHECK_QUERY = """
SELECT
    drug_code,
    "type",
    cub_rack,
    CASE WHEN qty_checked::text ~ '^[0-9]+(\\.[0-9]+)?$' THEN qty_checked::text::numeric END AS qty_checked,
    checked_by,
    to_timestamp(NULLIF("created_at"::text, 'NA')::bigint / 1000) AT TIME ZONE 'Asia/Kolkata' AS created_at,
    to_timestamp(NULLIF("time"::text, 'NA')::bigint / 1000)       AT TIME ZONE 'Asia/Kolkata' AS "time"
FROM "checking_history"
WHERE NULLIF("created_at"::text, 'NA')::bigint >= %(start_ms)s
  AND NULLIF("created_at"::text, 'NA')::bigint <  %(end_ms)s
ORDER BY NULLIF("created_at"::text, 'NA')::bigint;
"""


SAMPLE_QUERY = """
SELECT
    assembler,
    packer,
    details,
    status,
    to_timestamp(NULLIF(created_at::text, 'NA')::bigint / 1000)     AT TIME ZONE 'Asia/Kolkata' AS created_at,
    to_timestamp(NULLIF(shipping_time::text, 'NA')::bigint / 1000)  AT TIME ZONE 'Asia/Kolkata' AS shipping_time
FROM "testing_sample"
WHERE NULLIF(created_at::text, 'NA')::bigint >= %(start_ms)s
  AND NULLIF(created_at::text, 'NA')::bigint <  %(end_ms)s
ORDER BY NULLIF(created_at::text, 'NA')::bigint;
"""
# Same table and fields as extract_testing.py. `details` (JSON: SKU -> quantity) is counted in Python, exactly as that script does.


INV_QUERY = """
SELECT
    drug_code,
    checked_by,
    to_timestamp(NULLIF(verify_start::text, 'NA')::bigint / 1000)  AT TIME ZONE 'Asia/Kolkata' AS verify_start,
    to_timestamp(NULLIF(verify_finish::text, 'NA')::bigint / 1000) AT TIME ZONE 'Asia/Kolkata' AS verify_finish,
    to_timestamp(NULLIF(checked_time::text, 'NA')::bigint / 1000)  AT TIME ZONE 'Asia/Kolkata' AS checked_time
FROM "testing_details"
WHERE NULLIF(verify_start::text, 'NA') IS NOT NULL
  AND NULLIF(verify_start::text, 'NA')::bigint >= %(start_ms)s
  AND NULLIF(verify_start::text, 'NA')::bigint <  %(end_ms)s
ORDER BY NULLIF(verify_start::text, 'NA')::bigint;
"""

# Quantity from tbt_differences, matched afterwards on drug_code + the stamp that equals checked_time.
# If the stamp is not epoch milliseconds the match finds nothing and quantity stays blank - ratings do not use it.
TBT_QUERY = """
SELECT
    drug_code,
    quantity_states AS quantity_stated,
    q_found,
    CASE WHEN "timestamp"::text ~ '^[0-9]+$'
         THEN to_timestamp("timestamp"::text::bigint / 1000) AT TIME ZONE 'Asia/Kolkata' END AS tbt_time
FROM "TBT_differences"
WHERE "timestamp"::text ~ '^[0-9]+$'
  AND "timestamp"::text::bigint >= %(start_ms)s
  AND "timestamp"::text::bigint <  %(end_ms)s;
"""


RTO_SORT_QUERY = """
SELECT
    order_id,
    carton_added_by,
    to_timestamp(NULLIF(carton_added_at::text, 'NA')::bigint / 1000) AT TIME ZONE 'Asia/Kolkata' AS carton_added_at
FROM "return_shipment_tracking"
WHERE NULLIF(carton_added_at::text, 'NA') IS NOT NULL
  AND NULLIF(carton_added_at::text, 'NA')::bigint >= %(start_ms)s
  AND NULLIF(carton_added_at::text, 'NA')::bigint <  %(end_ms)s
ORDER BY NULLIF(carton_added_at::text, 'NA')::bigint;
"""
# Phase 1 (carton sorting): the same table and fields as extract_rto.py, only parcels sorted inside the window.

RTO_AUDIT_QUERY = """
SELECT
    id,
    workflow_step,
    performed_by,
    carton_number,
    item_count,
    to_timestamp(NULLIF(started_at::text, 'NA')::bigint / 1000)       AT TIME ZONE 'Asia/Kolkata' AS started_at,
    to_timestamp(NULLIF(last_activity_at::text, 'NA')::bigint / 1000) AT TIME ZONE 'Asia/Kolkata' AS last_activity_at,
    to_timestamp(NULLIF(ended_at::text, 'NA')::bigint / 1000)         AT TIME ZONE 'Asia/Kolkata' AS ended_at,
    end_reason,
    item_details
FROM "return_shipment_tracking_audit";
"""
# Phase 2 (trolley transfer + merge): same table and fields as extract_rto2.py. The audit table is small (a few hundred sessions),
# so it is read whole and cut to the period in Python; item_details (JSON list of {at, batch, quantity, drug_code}) is read in rto.normalize_audit.


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


@st.cache_data(ttl=900, max_entries=30, show_spinner="Reading the selected dates from the database...")
def fetch_checking(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Checking: only tasks started (created_at) inside the window are read. Cached per window for 15 minutes."""
    df = _fetch(CHECK_QUERY, {"start_ms": int(start_ms), "end_ms": int(end_ms)})
    for c in ["created_at", "time"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["qty_checked"] = pd.to_numeric(df["qty_checked"], errors="coerce")
    return df


@st.cache_data(ttl=900, max_entries=30, show_spinner="Reading the selected dates from the database...")
def fetch_samples(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Sample creation: only samples created inside the window are read. Cached per window for 15 minutes."""
    from sample import count_details
    df = _fetch(SAMPLE_QUERY, {"start_ms": int(start_ms), "end_ms": int(end_ms)})
    counts = df["details"].map(count_details)
    df["distinct_skus"] = counts.map(lambda t: t[0])
    df["total_items"] = counts.map(lambda t: t[1])
    df = df.drop(columns=["details"])
    for c in ["created_at", "shipping_time"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


@st.cache_data(ttl=900, max_entries=30, show_spinner="Reading the selected dates from the database...")
def fetch_inventory(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Inventory: verifications started (verify_start) inside the window, with quantity found from tbt_differences."""
    pad = 24 * 3600 * 1000   # the quantity row is stamped within seconds of checked_time; read a day either side to be safe
    v = _fetch(INV_QUERY, {"start_ms": int(start_ms), "end_ms": int(end_ms)})
    for c in ["verify_start", "verify_finish", "checked_time"]:
        v[c] = pd.to_datetime(v[c], errors="coerce")
    for c in ["quantity_stated", "q_found"]:
        v[c] = np.nan
    v["qty_note"] = ""
    if v.empty:
        return v
    try:
        q = _fetch(TBT_QUERY, {"start_ms": int(start_ms) - pad, "end_ms": int(end_ms) + pad})
        q["tbt_time"] = pd.to_datetime(q["tbt_time"], errors="coerce")
        q = q.dropna(subset=["tbt_time"]).sort_values("tbt_time").drop_duplicates(["drug_code", "tbt_time"])
        v = v.drop(columns=["quantity_stated", "q_found"]).sort_values("checked_time")
        # exact stamp match, with a 3 second tolerance in case the two tables round the stamp differently
        v = pd.merge_asof(v, q, left_on="checked_time", right_on="tbt_time", by="drug_code",
                          tolerance=pd.Timedelta("3s"), direction="nearest").drop(columns=["tbt_time"]).sort_values("verify_start").reset_index(drop=True)
        v["qty_note"] = "" if v["q_found"].notna().any() else "no matching rows in tbt_differences"
    except Exception as e:
        v["qty_note"] = f"quantity not read ({type(e).__name__})"   # quantity is reference only; ratings do not need it
    return v


@st.cache_data(ttl=900, max_entries=30, show_spinner="Reading the selected dates from the database...")
def fetch_rto_sort(start_ms: int, end_ms: int) -> pd.DataFrame:
    """RTO Phase 1: parcels sorted into cartons (carton_added_at) inside the window."""
    df = _fetch(RTO_SORT_QUERY, {"start_ms": int(start_ms), "end_ms": int(end_ms)})
    df["carton_added_at"] = pd.to_datetime(df["carton_added_at"], errors="coerce")
    return df


@st.cache_data(ttl=900, max_entries=4, show_spinner="Reading the RTO audit table from the database...")
def fetch_rto_audit():
    """RTO Phase 2: every audit session (trolley transfer / merge) and its merged items, normalised."""
    import rto
    raw = _fetch(RTO_AUDIT_QUERY, {})
    return rto.normalize_audit(raw)


def load_csv(file_or_path) -> pd.DataFrame:
    """Load the CSV written by extract_data.py (or the workbook's source CSV)."""
    df = pd.read_csv(file_or_path)
    for c in ["make_list_created_at", "created_at", "updated_at", "subtraction_time"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df
