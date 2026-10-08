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
