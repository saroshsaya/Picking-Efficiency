"""Extract the DTOs for DTO_Efficiency.xlsx.

Reads one table (dto_shipment_tracking), read-only, and writes dto_for_workbook.xlsx: exactly the columns
A to G of the DTO Calc sheet (phone numbers left out, AWB kept as text, times to the second, in IST).

What changed from extract_dto.py
  * Times are converted by size, not by one formula: a 13-digit value is milliseconds, a 10-digit value is
    seconds. The old query divided everything by 1000, which cut the newer (10-digit) values to about 17 minutes.
  * No passwords in this file. Each value is read from an environment variable; if it is not set, the script asks.

Environment variables (all optional; the script asks for anything missing)
  SAYA_SSH_HOST, SAYA_SSH_PORT (22), SAYA_SSH_USER, SAYA_SSH_PASSWORD
  SAYA_DB_NAME, SAYA_DB_USER, SAYA_DB_PASSWORD
  SAYA_DB_BIND_HOST (127.0.0.1), SAYA_DB_BIND_PORT (5432)

Needs:  pip install pg8000 sshtunnel openpyxl
Run:    python extract_dto_v2.py
"""
import getpass
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OUTPUT_FILE = "dto_for_workbook.xlsx"
HEADERS = ["DTO no.", "Customer and order no. (15 = first order)", "AWB", "Start (spt)", "End (trh)",
           "Accepted (Accept Return)", "Accepted by (login)"]

# Every value is read as text and converted here, because the stored times are mixed milliseconds and seconds.
QUERY = """
SELECT phone_number::text, order_id::text, awb::text, accepted_by::text,
       accepted_at::text, spt::text, trh::text
FROM "dto_shipment_tracking";
"""


def to_ist(value):
    """Epoch text -> naive IST datetime to the second, or None.
    13 digits = milliseconds, 10 digits = seconds. 'NA', 'NaN', empty or any other length -> None."""
    if value is None:
        return None
    s = str(value).strip()
    m = re.fullmatch(r"(\d+)(\.\d+)?", s)
    if not m:
        return None
    digits = len(m.group(1))
    x = float(s)
    if digits == 13:
        x /= 1000.0
    elif digits != 10:
        return None
    return datetime.fromtimestamp(round(x), tz=IST).replace(tzinfo=None)


def clean_text(value):
    if value is None:
        return None
    s = str(value).strip()
    return None if s in ("", "NA", "NaN", "None", "nan") else s


def customer_code(phone, order_id):
    """Short stable label such as 'C04817 / 15'. The phone number itself never leaves this script."""
    p = re.sub(r"\D", "", str(phone or ""))
    code = int(hashlib.sha1(p.encode()).hexdigest(), 16) % 100000 if p else 0
    return "C%05d / %s" % (code, clean_text(order_id) or "?")


def build_rows(raw):
    """raw: iterable of (phone, order_id, awb, accepted_by, accepted_at, spt, trh) as text.
    Returns the rows for DTO Calc A to G, oldest start first, numbered D001, D002, ..."""
    out = []
    for phone, order_id, awb, accepted_by, accepted_at, spt, trh in raw:
        out.append([customer_code(phone, order_id), clean_text(awb), to_ist(spt), to_ist(trh),
                    to_ist(accepted_at), clean_text(accepted_by)])
    far = datetime(2100, 1, 1)
    out.sort(key=lambda r: (r[2] or far, r[4] or far, r[1] or ""))
    return [["D%03d" % (i + 1)] + r for i, r in enumerate(out)]


def setting(env_name, label, secret=False, default=None):
    value = os.environ.get(env_name)
    if value:
        return value
    prompt = label + (" [%s]" % default if default else "") + ": "
    value = getpass.getpass(prompt) if secret else input(prompt)
    return value.strip() or default


def fetch_raw():
    """Read-only query through the SSH tunnel. The imports are here so the conversion code can be tested without them."""
    import pg8000.dbapi
    from sshtunnel import SSHTunnelForwarder

    ssh_host = setting("SAYA_SSH_HOST", "SSH host")
    ssh_port = int(setting("SAYA_SSH_PORT", "SSH port", default="22"))
    ssh_user = setting("SAYA_SSH_USER", "SSH user")
    ssh_password = setting("SAYA_SSH_PASSWORD", "SSH password", secret=True)
    db_name = setting("SAYA_DB_NAME", "Database name")
    db_user = setting("SAYA_DB_USER", "Database user")
    db_password = setting("SAYA_DB_PASSWORD", "Database password", secret=True)
    bind_host = setting("SAYA_DB_BIND_HOST", "Database host as seen from the SSH server", default="127.0.0.1")
    bind_port = int(setting("SAYA_DB_BIND_PORT", "Database port", default="5432"))

    print("Opening SSH tunnel...")
    with SSHTunnelForwarder((ssh_host, ssh_port), ssh_username=ssh_user, ssh_password=ssh_password,
                            remote_bind_address=(bind_host, bind_port)) as tunnel:
        conn = pg8000.dbapi.connect(host=tunnel.local_bind_host, port=tunnel.local_bind_port,
                                    user=db_user, password=db_password, database=db_name)
        try:
            cur = conn.cursor()
            cur.execute("SET TRANSACTION READ ONLY")          # this script can only read
            cur.execute("SET statement_timeout = '60s'")      # and cannot hold the database for long
            cur.execute(QUERY)
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
    return rows


def write_xlsx(rows, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "DTO Calc A to G"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        row[2].number_format = "@"                              # AWB as text
        for c in row[3:6]:
            c.number_format = "dd-mmm-yy hh:mm:ss"
    for col, w in zip("ABCDEFG", [8, 22, 18, 20, 20, 20, 32]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    wb.save(path)


def main():
    raw = fetch_raw()
    rows = build_rows(raw)
    write_xlsx(rows, OUTPUT_FILE)
    both = sum(1 for r in rows if r[3] and r[4])
    print("Saved %d DTOs to %s (%d have both a start and an end)." % (len(rows), OUTPUT_FILE, both))
    print("Next: in DTO Calc clear columns A to G from row 5 down, then paste these rows (without the header) at A5.")


if __name__ == "__main__":
    main()
