# Picking efficiency dashboard

Compares each executive's actual picking pace with the average recorded in the time study
(4.78 sec/item) - daily, weekly and monthly - and flags make lists worth investigating.
Same logic as the Excel workbook (`Picking_Efficiency.xlsx`).

## Layout (what people see)
* The app opens on a **Home** screen. No activity loads until it is chosen from the cards or from the bar at the top, which fills the window width.
* Each activity shows two numbers (efficiency and the work done), an Overview table, Weekly / Monthly (efficiency table and chart only), Review flags (counts and reasons; the individual rows are in an expander) and How it works.
* **Admin tools** (switch at the bottom of Home, off by default) brings back the Settings button, the data checks, the packing by-hour tables, the fit notes and the picking list inspector. With it off every page runs on the standards in `config.py`.
* `EXCLUDED_LOGINS` in `config.py` (Mohit, Devendra) is left out of every activity, including past weeks.

## Files
| File | What it does |
|---|---|
| `app.py` | Entry point: page setup, optional password, and the Picking / Packing navigation |
| `home_page.py` | Home screen: one card per activity and the Admin tools switch. Opens first and reads nothing from the database |
| `picking_page.py` | The Picking screens (settings popover, period picker, tabs) |
| `packing_page.py` | The Packing screens (same layout, plus a Bottlenecks tab) |
| `checking_page.py` | The Checking screens (same layout) |
| `inventory_page.py` | The Inventory (slot verification) screens (same layout) |
| `sample_page.py` | The Sample creation screens (same layout, no Daily / Bottlenecks tabs: samples are few) |
| `dto_page.py` | The DTO (customer returns via courier) screens (same layout, tabs Overview / Weekly / Monthly / Review flags / How it works) |
| `move_page.py` | The Move (LT rack to ST cubicle) screens (same layout as DTO) |
| `assembly_page.py` | The Assembly screens (same layout as DTO) |
| `efficiency.py` | **Picking rules** - pure pandas, no Streamlit, no database |
| `packing.py` | **Packing rules** - pure pandas, no Streamlit, no database |
| `checking.py` | **Checking rules** - pure pandas, no Streamlit, no database |
| `inventory.py` | **Inventory rules** - pure pandas, no Streamlit, no database |
| `sample.py` | **Sample-creation rules** - pure pandas, no Streamlit, no database |
| `dto.py` | **DTO rules** - pure pandas, no Streamlit, no database |
| `move.py` | **Move rules** - pure pandas, no Streamlit, no database |
| `assembly.py` | **Assembly rules** - pure pandas, no Streamlit, no database |
| `db.py` | Database queries (picking lines; packing orders already joined to their item counts) and the CSV loader |
| `ui_common.py` | Password gate and the efficiency colouring shared by both pages |
| `config.py` | Names (login -> name), notes, time-study averages and the packing standard |
| `tests/` | tests on tiny synthetic data - run `python -m pytest tests` |
| `.streamlit/secrets.toml.example` | Template for the credentials - copy and fill in, never commit the real one |

## How it reads the database (light on the DB)
* The page starts **blank**. Choose a **Period** (last 7 / 30 / 90 days, this month, last month, or **Custom**, which shows the From / To boxes) and press **Load** - nothing is read from the database before that.
* Only the make lists **created inside that period** are read - the dates go into the SQL `WHERE` clause, so the whole table is never pulled.
  (The query reads 60 seconds beyond each edge so a list on the boundary is not cut in half; lists that start outside the period are then dropped.)
* Each period is cached for 15 minutes, so changing tabs, settings or the executive filter does not touch the database again.
  **Refresh data** clears the cache. If you pick a different period, the page keeps showing the loaded one until you press **Load** again.
* The **☰ Settings** button holds every setting from the Excel `Settings` sheet. It is shown only when **Admin tools** is switched on (Home screen).
* For the backend team: the date columns are stored as text (`'NA'` when empty), so the filter casts them. The database still has to
  look through the table to apply it, which is trivial today but grows with the table. This index makes the filter cheap forever:
  ```sql
  CREATE INDEX CONCURRENTLY idx_areab_make_list_created_ms
    ON "master_areaB_way_status" (((NULLIF(make_list_created_at, 'NA'))::bigint));
  ```

## The picking standard (pieces + distinct SKUs)
A list's time is mostly walking to and picking from each SKU slot, and only a little per piece. So the standard is
`scale x (1.78 x pieces + 23.62 x distinct SKUs picked)` seconds. The two numbers were fitted on ~1,150 rated lists **within each
person** (everyone gets their own base level), because fast people tend to take the big morning lists and fitting everyone
together would under-state the per-piece time.
In the morning a list has about 6-8 pieces per SKU and in the evening about 4-5, so a flat seconds-per-piece rate flatters morning
lists and penalises evening ones; the SKU term removes that bias (hour-to-hour variation in efficiency fell from 0.29 to 0.15).
* **Scale** (Settings > Standard level): *Typical actual pace* (default, scale 0.93) puts the team as a whole near 100%, like Packing;
  *Time-study pace* is stricter and reproduces your 20 recorded study lists, so everyone sits well below 100%.
* **Standard model > Pieces only (old)** switches back to the flat recorded rate.
* The Settings panel shows what the loaded period would fit to, so you can see when the numbers need refreshing.

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

## Checking
* Reads `checking_history` for tasks whose **created_at** (task started) falls in the chosen period; only those rows come back.
* Two kinds of checking: **ID checking** (ID_CUBICLE, ID_RACK) is held to one standard time (29.8 s, the mean of rated ID checks);
  **quantity checking** (ST, LT) to `30.87 + 11.79 x LN(1 + quantity)` seconds. There is no time study, so both come from past checks.
* A task over 5 minutes is flagged and not rated; a task with a missing timestamp is not considered; a quantity check without a quantity has no standard.
  The overall efficiency per checker adds both activities; their own columns show each separately.
* Suggested index for the backend team:
  ```sql
  CREATE INDEX CONCURRENTLY idx_checking_history_created_ms ON checking_history (((NULLIF("created_at"::text, 'NA'))::bigint));
  ```

## Run it on your computer
```
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml     (then edit it)
streamlit run app.py
```
There is no CSV upload: every page reads the live database. (For development only, `DEV_CSV_PATH` points Picking at a file written by `extract_data.py`.)
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
All thresholds are in **Settings** (Admin tools); the **How it works** tab explains them.

## Notes
* With millisecond times from the database the 2-second merge rule is precise. The Excel workbook was built on minute-only
  times, so small differences (about 0.1-0.3 percentage points) are expected.
* A lead-in of exactly 10.0 minutes is not "above 10", so it is not flagged here (Excel's floating-point arithmetic
  flags three such lists).
* Assembly is not measured: there are no usable assembly timestamps.
* `subtraction_time` is read and checked but not used in the calculations.

## Sample creation
* Reads `testing_sample` (same fields as `extract_testing.py`) for samples **created** in the chosen period. The `details` JSON (SKU -> quantity) is counted in Python, as in that script.
* **Time** = `shipping_time` - `created_at`. **Person** = the packer. **Size** = items only (no SKU count, no label time).
* **Standard** = 3,057.96 s + 43.27 s per item (`SAMPLE_STD_A`, `SAMPLE_STD_B` in `config.py`), fitted on same-day past samples so the team is near 100%.
  The Overview tab shows what the loaded period would fit to.
* Rated = status past, packer on the team list, both stamps, same calendar day (switchable), items > 0. Logins in `SAMPLE_EXCLUDED_LOGINS` are not rated (tick the box in Settings to include them).
* Suggested index for the backend team: `CREATE INDEX CONCURRENTLY idx_testing_sample_created_ms ON testing_sample (((NULLIF(created_at::text, 'NA'))::bigint));`

## Inventory (slot verification)
* Reads `testing_details` for verifications whose **verify_start** falls in the chosen period (only rows that have a `verify_start`; stamps exist since 18 Aug 2026), plus the quantity found from `TBT_differences`,
  matched on `drug_code` and the stamp that equals `checked_time`. If the quantity cannot be matched it stays blank - the ratings do not use it.
* **Time** = `checked_time` - `verify_start`. **Person** = `checked_by` (the same person verifies and checks).
* **Standard** = one fixed 414.3 s per verification (`INV_STD_SEC` in `config.py`): the mean of same-day verifications up to a 30-minute cap (`INV_MAX_SEC`), so the team is near 100%.
  Quantity found explains almost none of the time (R-squared about 0.06), so there is no quantity term; the Overview tab shows the current R-squared.
* Longer-than-cap and overnight verifications are not rated and are listed on Review flags. People with fewer than 10 rated cartons show no percentage (Settings).
* Names come from `LOGIN_NAMES` - note `deepak86650@gmail.com` is **Devendra**.
* Suggested index for the backend team: `CREATE INDEX CONCURRENTLY idx_testing_details_verify_ms ON testing_details (((NULLIF(verify_start::text, 'NA'))::bigint));`

## RTO (returned parcels)
One process, shown as one result; behind the scenes it is two phases (same rules and numbers as `RTO_Efficiency_v3.xlsx`).
* **Phase 1 - carton sorting.** Reads `return_shipment_tracking` for parcels whose **carton_added_at** falls in the chosen period (only those rows come back). Time = gap since the same person's previous `carton_added_at` (person = `carton_added_by`); a gap over 300 s is a break and is not rated. Standard = 10.6 s per parcel.
* **Phase 2 - trolley transfer + merge to base, carton by carton.** Reads the whole `return_shipment_tracking_audit` table (a few hundred sessions; it is cut to the period in Python). A job starts at the Trolley Transfer session start and ends at `ended_at` of the Merge session that follows for the same person and carton; only sessions that ended `auto_drained` are used (reset / superseded sessions hold no items).
  Parcels = the trolley session's `item_count`; drug codes and units come from the Merge session's `item_details` (distinct drug codes, sum of quantity).
  Standard = transfer (-23.76 + 103.91 x parcels + 2.19 x units) + merge (131.57 + 13.71 x drug codes + 1.62 x units) seconds - fitted on rated cartons so the team is near 100% (`RTO_*` in `config.py`; the Overview tab shows what the loaded period would fit to).
  A carton that took more than 2.5 x its own standard, or more than 4 hours, is a break or operational problem and is not rated (about 97% of cartons and gaps are rated).
* **Combined result** = (Phase 1 + Phase 2 standard seconds) / (Phase 1 + Phase 2 actual seconds). Phase 1 is counted from `RTO_COMBINED_FROM` (3 Sep 2026, the first day with Phase 2 data) so both phases cover the same days. Tabs: Overview, Daily, Weekly, Monthly, Review flags, How it works.
* **Anurag** (`ay9936370@gmail.com`) only scans parcels in and is not rated, with `admin` and `lakshya@sayacare.in` (`RTO_EXCLUDED_LOGINS` in `config.py`).
* The audit table's timestamps may be epoch milliseconds or real timestamps, and `item_details` may be a list of objects or an object keyed by drug code - `rto.normalize_audit` handles both. If the table cannot be read, the page still shows Phase 1 and says so.
* Suggested indexes for the backend team:
  ```sql
  CREATE INDEX CONCURRENTLY idx_rst_carton_added_ms ON return_shipment_tracking (((NULLIF(carton_added_at::text, 'NA'))::bigint));
  ```

## DTO (customer returns sent back by courier)
Same rules and numbers as `DTO_Efficiency.xlsx`.
* **Reads** one small table, `dto_shipment_tracking`, and only five columns (`awb`, `accepted_by`, `accepted_at`, `spt`, `trh`). No phone numbers or names are read. It is about 100 rows today, cached for 15 minutes, and cut to the chosen period in Python (`db.fetch_dto`).
* **Time** = `spt` (Start Return Processing) to `trh` (Return Handled). Courier time and restocking after Return Handled are not counted. **Person** = the login that accepted the DTO (`accepted_by`); names come from `LOGIN_NAMES` and `DTO_LOGIN_NAMES` in `config.py`.
* **Standard** = one flat **147 seconds** per DTO (`DTO_STD_SEC`): the average of the 17 DTOs timed on 17-22 Oct 2026 (Akash and Sunny). It includes the one DTO with an issue (photos added - part of the DTO process, so no extra allowance) and the slowest one, so a trouble-free DTO shows as efficient and a DTO with an issue costs little. The page's Settings popover has an optional allowance if you ever want more.
* **Rated** = both stamps present, end not before start, and 10 s to 1800 s. Efficiency = sum of standard seconds / sum of actual seconds over rated DTOs. A person or the team needs 20 rated DTOs to show a percentage; a week or month cell needs 5; cells under 15 are grey italics.
* **Stored times are a mix of units.** The older module writes epoch milliseconds (13 digits), the newer one epoch seconds (10 digits). `dto.epoch_to_ist` reads them as text and converts by length. Do not divide every value by 1000: that cuts the 10-digit ones to about 17 minutes in 1970 and most starts then equal their ends.
* **Item counts are not used yet.** The aim is to count the total items and the SKUs in a DTO, but the table has no item counts. Once the planned `dto_issues` table is linked to a DTO, the standard can become "base + seconds per unit" (the study fit was about 92 s + 8.7 s per unit, R-squared about 0.38) - say so and it will be added.
* The Review flags tab lists slow (over 3 x the standard), too-long and end-before-start DTOs; the How it works tab has a Data checks box (for example how many starts equal the Accept Return time - high would mean Start Return Processing is not a separate click).
* To try the page without the database, set `DEV_DTO_CSV` to a CSV with columns `awb, accepted_by, accepted_at, spt, trh` (epoch values as stored).

## Move (stock moved from an LT rack to an ST cubicle)
* **Reads** the `past_moves` table for the chosen period only (moves submitted in it): `moved_by`, `DC`, `cumulative_change`, `move_start`, `move_time`. About a thousand moves a month, cached for 15 minutes (`db.fetch_moves`).
* **Time** = `move_start` (MOVE pressed) to `move_time` (SUBMIT pressed), per drug code. **Person** = `moved_by`. **Size** = units moved (`cumulative_change` - confirm this is the quantity moved).
* **The start stamp is new.** `move_start` is filled only from 12 Nov 2026 (`MOVE_START_FROM` in `config.py`). Earlier moves are counted but cannot be timed, and the page says how many.
* **Standard** = `MOVE_STD_FIXED` + `MOVE_STD_PER_UNIT` x units moved, from the move time study. Both are 0 until they are filled in: with 0 the page shows counts and times but no percentage. They can also be typed in the Settings popover.
* **Rated** = has a start, submit not before start, between `MOVE_MIN_SEC` (2 s) and `MOVE_MAX_SEC` (1800 s), and units recorded. Efficiency = sum of standard seconds / sum of actual seconds over rated moves; a person or the team needs 20 rated moves to show a percentage, a week or month cell 5, cells under 15 are grey italics.
* **Check the start stamp first.** The first timed moves (12-13 Nov 2026) took only 4 to 8 seconds from MOVE to SUBMIT, which is far too short for fetching a box and filling a cubicle. If `move_start` is stamped late (for example when the quantity screen opens), every percentage will look far too high. The page warns when half of the rated moves take under 15 seconds; the Data checks box shows the median.
* **Stored times** are read as text and converted by length (13 digits = milliseconds, 10 = seconds), as in the DTO tab. Do not divide every value by 1000 in an extract.
* To try the page without the database, set `DEV_MOVE_CSV` to a CSV with columns `moved_by, DC, cumulative_change, move_start, move_time` (the file written by `extract_move.py` works as it is).

## Assembly (first inventory subtraction of the order to TGR)
* **Reads** orders marked assembled (`TGR`) in the chosen period from `past_orders` and `cancelled_orders`, and joins each one **inside the database** to the earliest row of `inventory_subtractions` with the same phone number + order id. Only the assembler, `n_items`, the order id and the two times come back - no phone numbers. Cached for 15 minutes (`db.fetch_assembly`).
* **Time** = first inventory subtraction of the order to `TGR`. **Person** = assembler (`pharmacist`). **Size** = number of different drug codes in the order (`n_items`); quantities are not used.
* **Standard** = `ASM_STD_PER_CODE` (7.7 s) x drug codes, in `config.py`. It can also be changed in the Settings popover.
* **Rated** = every order, unless there is no assembler, no subtraction, no drug code count, the first subtraction is after TGR, or it took longer than `ASM_MAX_SEC` (900 s = 15 minutes). Efficiency = sum of standard seconds / sum of actual seconds over rated orders.
* **Left out**: the logins in `ASM_EXCLUDED_LOGINS` (Shivangi, Dhruv, Aryan, Mohit, Devendra).
* **Stamps** stored in seconds (10 digits) and in milliseconds (13 digits) are both read correctly.
* Orders still in `active_orders` have no TGR, so the last few days can look thin.
* To try the page without the database, set `DEV_ASSEMBLY_ORDERS_CSV` (the past and cancelled order extracts, comma-separated) and `DEV_ASSEMBLY_SUBS_CSV` (the subtraction extract).
