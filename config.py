"""Static configuration: executive names and the time-study benchmark.

Edit LOGIN_NAMES when a new executive starts. Logins that are not listed show
the part of the login before the "@".
"""

# login -> display name (order here = order shown in the tables)
LOGIN_NAMES = {
    "naagard47@gmail.com": "Deepak",
    "panindia135@gmail.com": "Vijay",
    "sunilkumar19902015@gmail.com": "Sunil",
    "kumarritik48822@gmail.com": "Ritik",
    "mohitpatel639049@gmail.com": "Mohit",
    "deepak86650@gmail.com": "Devendra",
    "jitindersingh9720@gmail.com": "Jitendra",
    "lexor0001@gmail.com": "Sahil",
    "ks1354933@gmail.com": "Kartik",
    "akashk00525@gmail.com": "Akash",
    "sunnyrajak618@gmail.com": "Sunny",
    "naduraj4848@gmail.com": "Nandu",
    "nanduraj4848@gmail.com": "Nandu",      # second login, assumed to be the same person
    "dmg@saya.net.in": "Dhruv",
}

# Notes shown next to an executive on the overview table
EXEC_NOTES = {
    "Devendra": "New executive - slower pace expected",
    "Jitendra": "Consistently fast",
}

# From the time study (Time_Study_PA.xlsx, 'Summary & Standards'):
STUDY_PICKING_SEC_PER_ITEM = 4.78      # Overall Picking Rate, 20 clean cycles
STUDY_ASSEMBLY_SEC_PER_ITEM = 4.63     # reference only - no assembly timestamps exist
STUDY_CYCLES = 20
STUDY_ITEMS = 3851


# ---- Packing (from the packing time study and the fitted standard) ----
PACK_STUDY_SEC_PER_ITEM = 7.73          # 30 cycles, 613 items, 79 minutes - reference only
PACK_STD_A = 44.01                      # fixed seconds per order
PACK_STD_B = 1.30                       # seconds per item
PACK_STD_C = 11.67                      # seconds per distinct SKU


# ---- Picking standard: time depends on distinct SKUs (walking to each slot) as well as pieces ----
PICK_STD_B_PIECE = 1.78        # seconds per piece        (fitted on rated lists, within each person)
PICK_STD_C_SKU = 23.62         # seconds per distinct SKU (walking to the slot, finding it, scanning it)
PICK_LEVEL_TYPICAL = 0.93      # scale that puts the team at about 100% on the lists the standard was fitted on
# The 20 picking study cycles took 307 minutes for 3,851 items; all lists have 48 SKUs (3 cycles left blank).
PICK_STUDY_SECONDS = 307 * 60
PICK_STUDY_ITEMS = 3851
PICK_STUDY_LISTS = 20
PICK_STUDY_SKUS_PER_LIST = 48


# ---- Checking (no time study: the standards come from past checks) ----
CHECK_ID_STD_SEC = 29.78        # ID checking (ID_CUBICLE, ID_RACK): mean seconds of rated tasks
CHECK_QTY_A = 30.87             # quantity checking (ST, LT): standard = a + b x LN(1 + quantity)
CHECK_QTY_B = 11.79
CHECK_MAX_SEC = 300             # tasks longer than 5 minutes are flagged and not rated


# ---- Sample creation (fitted on same-day samples; created_at -> shipping_time, packer, items only) ----
SAMPLE_STD_A = 3057.96          # fixed seconds per sample
SAMPLE_STD_B = 43.27            # seconds per item (every item is blacked out by hand; SKU count is not used)
# Logins that appear as packers in testing_sample but are not rated (left out of the earlier workbook too).
SAMPLE_EXCLUDED_LOGINS = {
    "jatinsingh71105@gmail.com", "dmg@saya.net.in", "shivangi12.301@gmail.com",
    "dhruvg001@gmail.com", "shalinidhiman1999@gmail.com", "akashsinghy318@gmail.com",
}


# ---- Inventory (slot verification): standard = one fixed time per verification ----
INV_STD_SEC = 414.3             # mean of same-day verifications up to the cap, so the team is near 100%
INV_MAX_SEC = 1800              # verifications longer than 30 minutes are flagged and not rated
# Quantity found does not explain the time (R-squared about 0.06), so no quantity term is used.
