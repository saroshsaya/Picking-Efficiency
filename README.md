# Picking efficiency dashboard

Compares each executive's actual picking pace with the average recorded in the time study
(4.78 sec/item) - daily, weekly and monthly - and flags make lists worth investigating.
Same logic as the Excel workbook (`Picking_Efficiency.xlsx`).

## Files
| File | What it does |
|---|---|
| `app.py` | Entry point: page setup, optional password, and the Picking / Packing navigation |
| `picking_page.py` | The Picking screens (settings popover, period picker, tabs) |
| `packing_page.py` | The Packing screens (same layout, plus a Bottlenecks tab) |
| `efficiency.py` | **Picking rules** - pure pandas, no Streamlit, no database |
| `packing.py` | **Packing rules** - pure pandas, no Streamlit, no database |
| `db.py` | Database queries (picking lines; packing orders already joined to their item counts) and the CSV loader |
| `ui_common.py` | Password gate and the efficiency colouring shared by both pages |
| `config.py` | Names (login -> name), notes, time-study averages and the packing standard |
| `tests/` | 23 tests on tiny synthetic data - run `python -m pytest tests` |
| `.streamlit/secrets.toml.example` | Template for the credentials - copy and fill in, never commit the real one |

## How it reads the database (light on the DB)
* The page starts **blank**. Choose a **Period** (last 7 / 30 / 90 days, this month, last month, or **Custom**, which shows the From / To boxes) and press **Load** - nothing is read from the database before that.
* Only the make lists **created inside that period** are read - the dates go into the SQL `WHERE` clause, so the whole table is never pulled.
  (The query reads 60 seconds beyond each edge so a list on the boundary is not cut in half; lists that start outside the period are then dropped.)
* Each period is cached for 15 minutes, so changing tabs, settings or the executive filter does not touch the database again.
  **Refresh data** clears the cache. If you pick a different period, the page keeps showing the loaded one until you press **Load** again.
* The **☰ Settings** button (top of the page) holds every setting from the Excel `Settings` sheet.
* For the backend team: the date columns are stored as text (`'NA'` when empty), so the filter casts them. The database still has to
  look through the table to apply it, which is trivial today but grows with the table. This index makes the filter cheap forever:
  ```sql
  CREATE INDEX CONCURRENTLY idx_areab_make_list_created_ms
    ON "master_areaB_way_status" (((NULLIF(make_list_created_at, 'NA'))::bigint));
  ```

## Packing and the database
* Packing reads `past_orders` for orders whose **TGS** (Advance clicked) falls in the chosen period and joins each one to its
  item and SKU counts in `saya_orders` (phone number + order_id) **inside the database**. Item and SKU counts come from the JSON
  `"order"` column (keys = SKUs, each with a `Quantity`). Only a table of order times and counts comes back - **no phone numbers**.
* An order whose phone number + order_id matches more than one `saya_orders` row gets no item counts (it shows as "no item counts").
* Suggested indexes for the backend team (they make the period filter cheap as the tables grow; on a copy of your data a 7-day
  packing query went from 0.7 s to 0.14 s with the first one):
  ```sql
  CREATE INDEX CONCURRENTLY idx_saya_orders_phone_order ON saya_orders (phone_number, order_id);
  CREATE INDEX CONCURRENTLY idx_past_orders_tgs_ms ON past_orders (((NULLIF("TGS", 'NA'))::bigint));
  CREATE INDEX CONCURRENTLY idx_areab_make_list_created_ms ON "master_areaB_way_status" (((NULLIF(make_list_created_at, 'NA'))::bigint));
  ```

## Run it on your computer
```
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml     (then edit it)
streamlit run app.py
```
No database access yet? Choose **Upload CSV** in the sidebar and upload the file written by `extract_data.py`.
(Use the CSV straight from the script - opening and re-saving it in Excel cuts the times to the minute.
If you do use such a CSV, set "Merge lines created within" to 0 in the sidebar.)

## Put it online (Streamlit Community Cloud)
1. Push this folder to a **private** GitHub repository (the `.gitignore` already keeps secrets and CSVs out).
2. On share.streamlit.io choose the repo and `app.py`.
3. In the app's **Settings > Secrets** paste the contents of your filled-in `secrets.toml`.
4. Set `app_password` in the secrets so the dashboard asks for a password.

## Security - please read
* The real passwords must live only in the host's Secrets box (or a local `secrets.toml`), never in the code or in GitHub.
* Use the **read-only** database user. For the SSH tunnel use a **limited user (ideally with a key), not root**.
* Any password that has been pasted into a chat, an email or a file that was shared should be changed.
* `extract_data.py` currently has the passwords written in it. Before it goes anywhere shared, read them
  from environment variables instead (`os.environ["DB_PASSWORD"]`, ...).

## The rules in short
1. Lines of one login created within 2 seconds of each other are one make list.
2. A line with a scan time (`updated_at`) was picked; pieces = sum of `quantity`.
3. A normal list is timed from creation to last scan. A list with a **long wait** (first scan more than 10 min after
   creation) or a **partial** list (under 90% of lines picked) is flagged and timed from its first scan plus a
   lead-in allowance (median lead-in of normal lists).
4. A list is **rated** if it has at least 60 pieces and took at most 90 minutes. Everything else still counts as work picked.
5. Efficiency = benchmark sec/item / actual sec/item, pooled over rated lists (add pieces and minutes, divide once).
All thresholds are in the sidebar **Settings**; the **How it works** tab explains them.

## Notes
* With millisecond times from the database the 2-second merge rule is precise. The Excel workbook was built on minute-only
  times, so small differences (about 0.1-0.3 percentage points) are expected.
* A lead-in of exactly 10.0 minutes is not "above 10", so it is not flagged here (Excel's floating-point arithmetic
  flags three such lists).
* Assembly is not measured: there are no usable assembly timestamps.
* `subtraction_time` is read and checked but not used in the calculations.
