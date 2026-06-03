# BTSM — Brisnet DRF Auto-Downloader

End-to-end working downloader for Brisnet PP Data Files (DRF format).
Last verified run: **68 / 68 files downloaded, 0 failed**.

This is Step 1 of the BTSM (BeTheSmartMoney.com) automation pipeline. It runs
unattended, idempotent (skips files already on disk), and only grabs files
that are actually finalized (post positions set).

---

## Files in this bundle

| File | Purpose |
|---|---|
| `brisnet_download_v3.py` | The working downloader. ~500 lines, single file, no other modules needed. |
| `.env.template` | Rename to `.env` and fill in your Brisnet credentials. |
| `README.md` | This file. |

---

## Quick start

1. **Install dependencies**
   ```cmd
   pip install selenium requests
   ```
   Selenium will manage chromedriver automatically (Selenium 4.10+).

2. **Configure credentials** — copy `.env.template` to `.env` and fill in:
   ```
   BRISNET_USER=your_username
   BRISNET_PASS=your_password
   ```

3. **Adjust `OUTPUT_DIR`** in `brisnet_download_v3.py` (top of file) if you
   don't want files saved to `C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads`.

4. **Run it**
   ```cmd
   python brisnet_download_v3.py
   ```
   First run takes ~30–60 seconds extra to copy your Chrome profile.
   Subsequent runs skip that step.

5. **Schedule it** (optional) — Windows Task Scheduler, daily around
   10–11 AM ET works well (most cards finalize by then).

---

## Folder layout (after running)

```
<your script folder>/
├── brisnet_download_v3.py
├── .env                              ← your credentials (gitignored)
├── .env.template
├── README.md
├── brisnet_download.log              ← rolling log
├── chrome_selenium_profile/          ← Chrome profile copy (auto-created)
│   └── Default/
│       ├── Cookies
│       ├── Login Data
│       └── ...
└── diag_*.png / diag_*.html          ← diagnostics dumped on failures only

C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads\
├── 20260508_BTP_DRS.DRF
├── 20260508_CD_DRS.DRF
├── 20260509_KEE_DRS.DRF
└── ...                               ← one file per track per date
```

---

## How it works (step by step)

```
   ┌────────────────────────────────────────────────────────────┐
   │ 1. Copy live Chrome profile -> chrome_selenium_profile/    │
   │    (one-time; Chrome 136+ blocks Selenium from using       │
   │     the live profile directly)                             │
   ├────────────────────────────────────────────────────────────┤
   │ 2. Launch Chrome via Selenium pointed at the COPY          │
   │    Anti-detection: hide navigator.webdriver, etc.          │
   │    Configure built-in downloader: auto-save to OUTPUT_DIR  │
   ├────────────────────────────────────────────────────────────┤
   │ 3. Navigate to brisnet.com/product/data-files/DRS          │
   ├────────────────────────────────────────────────────────────┤
   │ 4. Check is_logged_in() — body text contains "logout"?     │
   │    NO  -> try_typed_login() with .env credentials          │
   │    YES -> proceed                                          │
   ├────────────────────────────────────────────────────────────┤
   │ 5. Wait for AngularJS grid:                                │
   │    - Wait for .track.table-row elements                    │
   │    - Wait for scope.track.trackCode to populate            │
   ├────────────────────────────────────────────────────────────┤
   │ 6. extract_tracks() — read Angular scope on every row:     │
   │      track.trackCode, trackName, country, trackType,       │
   │      dayEvening                                            │
   │      track.availableDates[].productDate                    │
   │      track.availableDates[].availableProducts[]            │
   │        .productCode, .customerAvailability, .productStatus │
   │    Filter:                                                 │
   │      productCode === "DRS"                                 │
   │      customerAvailability === "View"  (you can download)   │
   │      productStatus === "F"            (FINAL — has PPs)    │
   ├────────────────────────────────────────────────────────────┤
   │ 7. For each (track, date) — build_url():                   │
   │      /product/download/{YYYY-MM-DD}/{PRODUCT}/             │
   │        {COUNTRY}/{TRACKTYPE}/{TRACKCODE}/                  │
   │        {D|E}/{RACE_NUMBER}/                                │
   ├────────────────────────────────────────────────────────────┤
   │ 8. download_via_browser():                                 │
   │      window.open(url, '_blank')                            │
   │      Switch to new tab (so download is "user-initiated")   │
   │      Wait for new file (no .crdownload partials)           │
   │      Close download tab, switch back                       │
   ├────────────────────────────────────────────────────────────┤
   │ 9. Rename downloaded file to                               │
   │      {YYYYMMDD}_{TRACK}_{DRS}.DRF                          │
   │    Skip if target already exists (idempotent reruns)       │
   └────────────────────────────────────────────────────────────┘
```

---

## The Angular scope schema (what we read)

Brisnet's data-files page is a single-page AngularJS app. The page DOM
doesn't carry the data we need as text — it lives on the per-row scope.
For every `<div class="track table-row">` element:

```javascript
angular.element(row).scope().track = {
    trackCode:  "BTP",          // 2-3 letter track abbreviation
    trackName:  "Belterra Park",
    country:    "USA",          // used in download URL
    trackType:  "TB",           // Thoroughbred / breed type — used in URL
    dayEvening: "D",            // D=day card, E=evening — used in URL
    availableDates: [
        {
            productDate: "2026-05-08T00:00:00",   // ISO; we slice [:10]
            availableProducts: [
                {
                    productCode:           "DRS",   // "DRS" = single-card PP
                    customerAvailability:  "View",  // "View" = downloadable
                    productStatus:         "F",     // "F" = Final (has PPs)
                                                   // "P" = Preliminary (no PPs)
                    raceNumber:            0,       // 0 = full card
                    // ... other fields we ignore
                },
                // ... other product types (DRX, etc.)
            ]
        },
        // ... more dates
    ]
}
```

**The three filter conditions are non-negotiable:**

| Filter | Why |
|---|---|
| `productCode === "DRS"` | DRS = the single-card past performance file format. Other codes (DRX, etc.) are different products. |
| `customerAvailability === "View"` | You only have access to files this says `"View"`. Files marked `"AddToCart"` are paid extras you haven't bought. |
| `productStatus === "F"` | `F` = Final. The icon shows lines through the document graphic, indicating post positions are set. `P` (preliminary) means entries-only — no post positions assigned yet. You don't want those for handicapping. |

---

## The download URL pattern (reverse-engineered)

There's no documented API. The pattern was discovered by intercepting network
traffic when manually clicking a download icon:

```
https://www.brisnet.com/product/download/{date}/{product}/{country}/{type}/{track}/{D|E}/{race}/

Example:
https://www.brisnet.com/product/download/2026-05-08/DRS/USA/TB/BTP/D/0/
                                          │          │   │   │  │   │  │
                                          │          │   │   │  │   │  └─ raceNumber (0=whole card)
                                          │          │   │   │  │   └──── dayEvening
                                          │          │   │   │  └──────── trackCode
                                          │          │   │   └─────────── trackType
                                          │          │   └─────────────── country
                                          │          └─────────────────── productCode
                                          └────────────────────────────── productDate (YYYY-MM-DD)
```

Trailing slash matters. Don't URL-encode anything.

---

## The 9 critical findings (gotchas that took hours to debug)

### 1. Chrome 136+ blocks `--user-data-dir` pointing at the live profile
**Symptom:** `DevToolsActivePort file doesn't exist` even when no Chrome is open.
**Cause:** Google deliberately broke this in Chrome 136 as an anti-automation measure.
**Fix:** Copy the live profile to a separate folder (`chrome_selenium_profile/Default/`)
and point Selenium at the copy. Cookies and login state still carry over because
we copy `Cookies`, `Login Data`, `Preferences`, and `Local State`.

### 2. The data isn't in the HTML
**Symptom:** `BeautifulSoup` finds zero download links. `view-source:` shows
empty rows.
**Cause:** It's an AngularJS SPA. The HTML is just a template; data is bound
to the scope at runtime.
**Fix:** Use `driver.execute_script()` and call `angular.element(el).scope()` to
read data off the live scope objects.

### 3. The parent scope doesn't have the track data — only the row scope does
**Symptom:** `angular.element(document.body).scope()` returns useful-looking
controllers but no `track` data.
**Fix:** Iterate `document.querySelectorAll('.track.table-row')` and read each
row's scope individually.

### 4. Angular needs time to populate the scope after the elements render
**Symptom:** `.track.table-row` exists but `scope.track` is undefined.
**Fix:** `wait_for_angular_data()` polls until at least one row has
`scope.track.trackCode` populated. ~3–10 seconds typically.

### 5. `requests` can't download the files even with the right cookies
**Symptom:** Download URL returns HTML (the login or error page) instead of a
DRF file. Status 200, wrong content-type.
**Cause:** Brisnet's Angular app attaches CSRF tokens and other state we can't
easily replicate from a `requests.Session`.
**Fix:** Have Chrome itself navigate to the download URLs. Configure Chrome's
built-in downloader to auto-save to `OUTPUT_DIR` (`download.default_directory` +
`download.prompt_for_download: False`). Browser session has everything it needs.

### 6. Downloads only fire if the navigation is "user-initiated"
**Symptom:** `driver.get(download_url)` shows the URL in the address bar but
no file appears.
**Fix:** Open download URLs in a new tab via `window.open(url, '_blank')` and
switch to the new tab. Chrome treats this as user-driven and triggers the download.

### 7. The wrong URL pattern wastes a lot of debugging time
**Symptom:** `cgi-bin/card.cgi?func=download&...` returns 200 + HTML, not a file.
**Fix:** The real pattern is `/product/download/...` with positional path
segments — see the URL section above. Discovered by intercepting network
traffic on a manual click.

### 8. Blank-icon files vs filled-icon files
**Symptom:** Some downloaded files had no past performance data — just
entries with no post positions assigned.
**Cause:** The grid shows two icon states. Filled icon (lines through the
document) = `productStatus === "F"` (Final, post positions set).
Blank icon = `productStatus === "P"` (Preliminary, entries only).
**Fix:** Hard filter on `productStatus === "F"`.

### 9. Windows cmd choked on Unicode arrows in log output
**Symptom:** `UnicodeEncodeError: 'charmap' codec can't encode character '\u2193'`
mid-run, stopping the script.
**Cause:** Windows cmd defaults to cp1252.
**Fix:** Stick to ASCII in log messages (`OK`/`FAIL`/`->` instead of ✓/✗/↓).
The file handler is also explicitly set to UTF-8.

---

## Why we don't use `requests` for downloads

You'll see the script imports `requests` but never actually uses it for the
file fetch. That import is vestigial — left in case you want to extend with
a `requests` fallback. As shipped, **all downloads go through Chrome.**
We tried `requests`-with-copied-cookies first; Brisnet's CSRF/session
plumbing rejected it. Browser-driven is slower (~1–2 sec per file) but
100% reliable.

---

## Why we copy the Chrome profile

Two reasons compounding into one:

1. Chrome 136+ refuses to launch with Selenium pointed at an in-use profile.
2. Even if you close all Chrome windows, Chrome often leaves background
   processes that hold the profile lock.

The profile copy lives in `chrome_selenium_profile/` next to the script.
Cookies and auth refresh into it after each successful login. If something
gets stuck (e.g., session corrupted), delete the folder and rerun — it'll
re-copy from your live profile on next run.

---

## Diagnostics on failure

If the script fails (no tracks found, login failed, etc.) it dumps:

- `diag_screenshot[suffix].png` — what Chrome saw at the failure point
- `diag_page[suffix].html` — full DOM at the failure point
- Log entries with current URL, cookie names, body text sample, and
  `.track.table-row` count

The `[suffix]` indicates which checkpoint failed: `_login`, `_no_grid`,
`_before_relogin`, `_login_fail`, `_final`.

---

## Maintenance / when things break

The fragile points, in order of likelihood:

1. **Brisnet changes the page structure.** Selectors like `.track.table-row`
   or scope field names like `trackCode` could change. If `extract_tracks()`
   returns 0 with no obvious cause, dump scope manually:
   ```python
   driver.execute_script("""
       var el = document.querySelector('.track.table-row');
       return JSON.stringify(angular.element(el).scope().track, null, 2);
   """)
   ```
2. **The download URL pattern changes.** Open DevTools → Network tab,
   click a download icon manually, and inspect the request URL. Update
   `build_url()`.
3. **Chrome breaks profile copying again.** They've changed the rules
   twice in 18 months. If `ensure_profile_copy()` fails, the path forward
   is usually to copy a few additional/different files from
   `User Data/Default/`.
4. **`.env` credentials wrong.** The script will fall through to typed
   login and fail with `Could not log in`. Check `.env`.
