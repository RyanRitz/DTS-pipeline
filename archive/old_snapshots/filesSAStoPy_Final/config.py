"""
BTSM Automation Pipeline — Configuration
=========================================
Edit this file each race day before running run_daily.py.
All other modules read from this config — no other files need editing.
"""

from pathlib import Path
from datetime import date

# =============================================================================
# RACE DAY SETTINGS  ← Edit these each race day
# =============================================================================

TRACK       = "KEE"          # 3-letter track code: KEE, CDX, SAR, DMR, GPX
RACE_DATE   = "0408"         # MMDD format
YEAR        = "2026"

# =============================================================================
# SCRATCHES  ← Will be automated via Equibase API (Phase 1 Step 3)
#               For now, list manual scratches as (race, program_number) tuples
# =============================================================================

MANUAL_SCRATCHES = [
    # (race, program_number)
    # Example: (2, "1"), (2, "6")
]

# =============================================================================
# BASE DIRECTORY — adjust if you move the project
# =============================================================================

BASE_DIR = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation")

# =============================================================================
# DERIVED PATHS — no need to edit below this line
# =============================================================================

RAW_DATA_DIR    = BASE_DIR / "raw_data"
COEFF_DIR       = BASE_DIR / "coefficients"
OUTPUT_DIR      = BASE_DIR / "output"
LOG_DIR         = BASE_DIR / "logs"
PIPELINE_DIR    = BASE_DIR / "pipeline"

# DRF input file  e.g. KEE0408.DRF
DRF_FILE = RAW_DATA_DIR / f"{TRACK}{RACE_DATE}.DRF"

# Output Excel file  e.g. KEE0408.xlsx
OUTPUT_XLSX = OUTPUT_DIR / f"{TRACK}{RACE_DATE}.xlsx"

# Output PDF file  e.g. KEE0408.pdf
OUTPUT_PDF  = OUTPUT_DIR / f"{TRACK}{RACE_DATE}.pdf"

# Log file  e.g. logs/KEE0408_2026.log
LOG_FILE = LOG_DIR / f"{TRACK}{RACE_DATE}_{YEAR}.log"

# =============================================================================
# EQUIBASE RACE-DAY CHANGES  (Phase 1 — scratches.py)
# =============================================================================

# Equibase per-track XML URL pattern
EQUIBASE_CHANGES_URL = f"https://www.equibase.com/static/latechanges/html/latechanges{TRACK}-USA.html"

# Equibase downloadable XML (requires login — set credentials in .env)
EQUIBASE_XML_URL = "https://www.equibase.com/premium/eqbLateChangeXMLDownload.cfm"

# Optional: set your Equibase credentials in a .env file in BASE_DIR
# EQUIBASE_USER=your_username
# EQUIBASE_PASS=your_password

# =============================================================================
# TRACK CODE MAPPINGS
# =============================================================================

TRACK_FULL_NAMES = {
    "KEE": "Keeneland",
    "CDX": "Churchill Downs",
    "SAR": "Saratoga",
    "DMR": "Del Mar",
    "GPX": "Gulfstream Park",
}

# =============================================================================
# SCORING MODEL SETTINGS
# =============================================================================

# Vig assumption for implied probability normalization
VIG = 1.2049

# Coefficient file naming convention: {track}{surface}{mmyyyy}{variant}.sas7bdat
# Python will auto-discover files in COEFF_DIR matching the active meet

# Active dirt models for this meet  (map to predicted variable names)
DIRT_MODELS = {
    "c": f"keedirt{RACE_DATE[:2]}{YEAR[-4:]}c.sas7bdat",   # keedirt042026c
    "n": f"keedirt{RACE_DATE[:2]}{YEAR[-4:]}n.sas7bdat",
    "s": f"keedirt{RACE_DATE[:2]}{YEAR[-4:]}s.sas7bdat",
    "r": f"keedirt{RACE_DATE[:2]}{YEAR[-4:]}r.sas7bdat",
}

# Active turf models for this meet
TURF_MODELS = {
    "s":  f"keeturf{RACE_DATE[:2]}{YEAR[-4:]}s.sas7bdat",
    "r":  f"keeturf{RACE_DATE[:2]}{YEAR[-4:]}r.sas7bdat",
    "hp": f"keeturf{RACE_DATE[:2]}{YEAR[-4:]}hp.sas7bdat",
    "lp": f"keeturf{RACE_DATE[:2]}{YEAR[-4:]}lp.sas7bdat",
}

# Active maiden models for this meet (keyed by res_marker number)
MAIDEN_MODELS = {
    1:   "kee_maid_0426_spst.sas7bdat",
    2:   "kee_maid_0426_spsd.sas7bdat",
    3:   "kee_maid_0426_sprt.sas7bdat",
    4:   "kee_maid_0426_sprd.sas7bdat",
    6:   "kee_maid_0426_msd.sas7bdat",
    8:   "kee_maid_0426_mrd.sas7bdat",
    9:   "kee_maid_0426_spt.sas7bdat",
    10:  "kee_maid_0426_spd.sas7bdat",
    12:  "kee_maid_0426_md.sas7bdat",
    13:  "kee_maid_0426_sps.sas7bdat",
    14:  "kee_maid_0426_spr.sas7bdat",
    15:  "kee_maid_0426_ms.sas7bdat",
    16:  "kee_maid_0426_mr.sas7bdat",
    "M": "kee_maid_0426_m.sas7bdat",
    "S": "kee_maid_0426_s.sas7bdat",
}

# Final score ensemble weights  (must sum to 1.0)
SCORE_WEIGHTS = {
    "score1": 0.50,   # dirt/maiden core models
    "score2": 0.25,   # maiden sprint/route
    "score3": 0.25,   # turf + dirt sprint/route
}
