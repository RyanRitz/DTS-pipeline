# BTSM Automation — Project Review

_Date: 2026-05-27_

## TL;DR

The **scoring core is impressively far along** — the SAS-to-Python translation of feature engineering, model variable construction, scoring (PROC SCORE replica), attribution, and Excel report generation is essentially complete and internally consistent (~6,200 lines across 9 modules). The **shell around that core is largely missing**: there's no `run_daily.py` orchestrator, no scratches module, no PDF generator, and no website-upload step. There are also two **blocking issues** before the pipeline can run end-to-end on your machine, plus one **security issue** to fix immediately.

## What's in the repo

| File | Lines | Purpose | State |
|---|---|---|---|
| `BTSM Automation Blueprint.md` | 214 | Architecture doc | Reference |
| `config.py` | 133 | Race-day settings, paths, model registry | Working |
| `brisnet_login_helper.py` | 128 | Captures Brisnet cookies via Chrome remote-debug | Working — **but leaks plaintext credentials** |
| `brisnet_download.py` | 716 | Playwright + requests fallback downloader | Working with leftover debug noise |
| `drf_schema.py` | 1,445 | The full ~1,435-column BRISnet PP schema | Pure data — complete |
| `ingest_drf.py` | 283 | CSV → DataFrame, type/date/name cleanup, coupled-entry fix | Working |
| `race_normalize.py` | 696 | Translation of the SAS `%ryan5()` macro (race-level means, `I*` ratios, `x*` residuals) | Working but has a Linux fallback path that breaks silently on Windows |
| `race_norm_vars.txt` | 1,482 | Variable list consumed by `race_normalize.py` | Data — complete |
| `features.py` | 1,020 | 8 feature-engineering blocks translating `scoring_KEE_APR26.sas` | Working |
| `model_vars.py` | 686 | Builds ~72 composite model inputs across 9 model families | Working |
| `score.py` | 650 | Loads `.sas7bdat` coefficients via pyreadstat, replays PROC SCORE, applies ensemble weights & vig | Working (one perf bottleneck) |
| `attribution.py` | 583 | Per-horse coefficient × feature contributions, race-centered, deduped by theme | Working |
| `output.py` | 593 | openpyxl-driven Excel with summary + per-race sheets | Working — **but Excel, not PDF** |
| `run_download.md` | 24 | `.bat` wrapper (mislabeled extension) for Task Scheduler | Working |
| `*.sas7bdat` (24 files) | — | Coefficient tables (one per dirt/turf/maiden submodel) | Files present, but **all 24 fail to open with pyreadstat in this sandbox** — they may have been corrupted during upload; please verify locally |

## End-to-end wiring

What's actually chained together right now:

```
brisnet_download.py
        ↓ (writes KEEMMDD.DRF)
ingest_drf.load_drf
        ↓
features.engineer_features ── calls ──► race_normalize.compute_race_normalizations
                                  └──► model_vars.build_model_vars
        ↓
score.run_scoring (reads .sas7bdat coefficient files from config.DIRT_MODELS / TURF_MODELS / MAIDEN_MODELS)
        ↓
attribution.add_attributions
        ↓
output.generate_excel  →  KEEMMDD.xlsx
```

That entire chain exists in code. What's missing is the **orchestrator that actually invokes it in order**, plus the dynamic-data and publishing arms of the Blueprint.

## Gaps vs the Blueprint

| Blueprint step | Status |
|---|---|
| 1. Auto-download BRISnet PPs | Done (`brisnet_download.py`) |
| 2. Pull scratches / jockey changes / track conditions via API | **Missing** — `scratches.py` is referenced in `features.py:666` and `:921` and via Equibase URLs in `config.py`, but the module doesn't exist. `HorsesRan` currently just equals `NumOfEntries` (pre-scratch). `JCKchngName` is hardcoded NaN. `dirt_condition`/`turf_condition` are passed as strings with no source. |
| 3. Merge static + dynamic data | Partial — merge points exist in `features.py`, but with no dynamic source they're no-ops |
| 4. Run scoring model in Python | Done (`score.py`) |
| 5. Generate PDFs | **Missing** — `output.py` produces XLSX only. No ReportLab/WeasyPrint stage. Excel uses Unicode bullets and emojis that won't survive Excel→print→PDF cleanly. |
| 6. Upload PDFs to BTSM | **Missing** — no SFTP/API/CMS code |
| 7. Schedule daily via Task Scheduler | Partial — `.bat` wrapper for downloads only; no daily-pipeline scheduled task |
| 8. Email/log summary | Logging hooks exist via `config.LOG_FILE`; no summary email |

## Blocking issues (must fix before first end-to-end run)

1. **No `run_daily.py`.** `config.py:4` references it (“before running run_daily.py”) but the file doesn't exist. Nothing chains the modules. This is the single highest-leverage thing to add — probably ~50 lines.
2. **Package-import mismatch.** `ingest_drf.py:26`, `features.py:28`, `features.py:60` import as `from pipeline.X import Y`, but the files are flat in `docs/`. Either move them under `pipeline/` with an `__init__.py`, or strip the `pipeline.` prefix. Pipeline will fail at import time otherwise.
3. **Linux sandbox path leak in `race_normalize.py:587, :624`.** Falls back to `/home/claude/race_norm_vars.txt`. On Windows this silently misses, returns a DataFrame with no `_ave` columns, and quietly cascades into thousands of missing `x*`/`I*` features — silently broken scoring, not a loud failure. Hard-code or resolve the path relative to the module.

## Security — fix today

`brisnet_login_helper.py:69-70` and `brisnet_download.py` contain **plaintext Brisnet credentials** baked into source (username + password). Delete them. Read from `.env` only. If this repo is on GitHub or anywhere shareable, rotate the password immediately.

## Code-quality observations

- **`score._proc_score` (score.py:294-301)** iterates rows with `df.at[]` — replace with a single `df[feature_cols].fillna(0).values @ coef_array` for ~100× speedup on larger cards. Not urgent at a 100-horse scale, but it's the obvious bottleneck.
- **`features.py:106-108`** re-runs Block 6 and Block 7 after a supplementary normalization pass. If intentional, it deserves a one-line comment; otherwise it's double work on the two biggest blocks.
- **`brisnet_download.py`** has a lot of debug instrumentation (HTML dumps, link iteration logs) that should be guarded behind a `--debug` flag — currently it's always-on noise.
- **`drf_schema.py`** preserves typos from the BRIS spec verbatim (e.g. `TrainerShowsCureentMeet`). That's the right call — they're load-bearing identifiers — but it's worth a comment so nobody "fixes" them.
- **No `requirements.txt`, no `__init__.py`, no tests.** Minimum pinned deps: `playwright, pandas, numpy, pyreadstat, openpyxl, beautifulsoup4, requests, python-dotenv`.
- **`.sas7bdat` files in this upload don't open with pyreadstat or sas7bdat** — the header bytes don't match either parser's magic. This may be a sandbox upload artifact; please verify on your local machine. If they really are unreadable, you'll need fresh exports from SAS.

## Recommended next moves, in order

1. **Today**: scrub credentials from `brisnet_login_helper.py` / `brisnet_download.py`, move them to `.env`, rotate the Brisnet password.
2. **Today**: confirm the `.sas7bdat` coefficient files open with `pyreadstat.read_sas7bdat()` on your Windows machine. If they don't, that's an upstream problem to solve before anything else matters.
3. Decide on the package layout (`pipeline/` vs flat) and fix all three `from pipeline.X` imports.
4. Fix the `/home/claude/race_norm_vars.txt` fallback in `race_normalize.py` so it resolves relative to the module.
5. Write `run_daily.py` — load config, call `load_drf` → `engineer_features` → `run_scoring` → `add_attributions` → `generate_excel`, log + exit code on each stage. Get one card running end-to-end.
6. Build a minimal `scratches.py` that at least reads `MANUAL_SCRATCHES` from config and produces the schema downstream expects. The Equibase scraper can come next; the schema contract is what unblocks the rest.
7. Add a `requirements.txt` and a `README.md` with one-line install + one-line "run KEE 0408" instructions.
8. Add PDF output (WeasyPrint from an HTML template is probably easier than ReportLab here, since you already produce structured per-race tables).
9. Add the website-upload step (need to know your hosting — SFTP vs CMS vs API).
10. Wire the daily pipeline into Task Scheduler.

Steps 1–5 get you to a working daily Excel. Steps 6–10 close the Blueprint.

## What I'd commend

The hard part is done. Translating a SAS scoring pipeline with this many segments (dirt × condition × age × distance, turf × pace shape, ten maiden families) into clean Python is a real piece of work, and the code is readable, vectorized where it matters, and faithful to the source. The attribution logic in particular — race-centering contributions, deduping by theme group, rotating synonyms across the card — is the kind of thing that's easy to do badly and you did it well. The orchestration shell around it is genuinely the smaller half.
