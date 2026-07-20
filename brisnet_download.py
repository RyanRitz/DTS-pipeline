"""
brisnet_download_v3.py
======================
Automated daily downloader for Brisnet PP Data Files (DRF format).

WORKING VERSION — final, verified end-to-end (68 files downloaded, 0 failed).

How it works (high level):
  1. Copies your live Chrome profile to a separate folder (Chrome 136+ blocks
     Selenium from using the live one directly).
  2. Launches Chrome via Selenium pointed at the copy. Cookies/login carry over.
  3. Navigates to https://www.brisnet.com/product/data-files/DRS
  4. Verifies you're logged in. If not, types credentials from .env and logs in.
  5. Reads the AngularJS scope on every `.track.table-row` element to extract:
        - trackCode, trackName, country, trackType, dayEvening
        - availableDates -> productDate (ISO)
        - availableProducts -> productCode, customerAvailability, productStatus
  6. Filters to only files where:
        productCode === "DRS"
        customerAvailability === "View"   (you have access to download)
        productStatus === "F"             (FINAL — has post positions set;
                                            blank document icons are P/preliminary)
  7. Builds the real download URL pattern (reverse-engineered from network traffic):
        /product/download/{YYYY-MM-DD}/{PRODUCT}/{COUNTRY}/{TRACKTYPE}/
                          {TRACKCODE}/{D|E}/{RACE_NUMBER}/
  8. Has Chrome itself navigate to each URL — Chrome auto-saves to OUTPUT_DIR
     (using `requests` directly fails because Brisnet's Angular app attaches
     CSRF tokens and other state we can't easily replicate).
  9. Renames the downloaded file to {YYYYMMDD}_{TRACK}_{DRS}.DRF
 10. Skip-if-exists, so reruns are idempotent.

Daily routine:
    python brisnet_download_v3.py

Requirements:
    pip install selenium requests
    Chrome installed on the system
    .env file with BRISNET_USER and BRISNET_PASS

See README.md for details.
"""

import os
import re
import json
import time
import shutil
import logging
from datetime import datetime
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

# Optional Gmail notifier — degrades gracefully if notify.py is missing or
# .env doesn't have NOTIFY_* set. Failures still get logged either way.
try:
    from notify import send_failure_email as _send_failure_email
    _NOTIFY_OK = True
except Exception as _notify_imp_err:
    _send_failure_email = None
    _NOTIFY_OK = False

# Collected failures for the end-of-run summary email. Each entry is a dict
# {kind, detail} so the email body can categorize them. Cleared at top of
# main() so re-runs don't carry state.
_FAILURES: list[dict] = []


def _record_failure(kind: str, detail: str) -> None:
    """Append a failure to the run-level list AND log it."""
    _FAILURES.append({"kind": kind, "detail": detail})


def _send_run_summary_email(downloaded: int, skipped: int) -> None:
    """
    Send a single email summarizing all failures in this run, if any.
    No-op when _FAILURES is empty, when notify is unavailable, or when
    notify.py can't reach Gmail. Silent failure — we don't want a broken
    notifier to take down the downloader.
    """
    if not _FAILURES:
        return
    if not _NOTIFY_OK or _send_failure_email is None:
        log.warning(
            "[notify] would have sent email about %d failure(s), but "
            "notify.py is not importable", len(_FAILURES),
        )
        return

    # Group failures by kind for a more readable email body
    by_kind: dict[str, list[str]] = {}
    for f in _FAILURES:
        by_kind.setdefault(f["kind"], []).append(f["detail"])

    # Count "real" failures separately from NOT_READY, which is expected
    # for distant-future race dates that Brisnet hasn't posted yet.
    real_failures = [f for f in _FAILURES if f["kind"] != "NOT_READY"]
    not_ready_count = len(_FAILURES) - len(real_failures)

    headline = (
        f"Brisnet downloader finished with {len(real_failures)} "
        f"real failure(s) and {not_ready_count} not-yet-published "
        f"card(s)."
        if not_ready_count else
        f"Brisnet downloader finished with {len(_FAILURES)} failure(s)."
    )

    lines = [
        headline,
        "",
        f"Summary:  Downloaded={downloaded}  Skipped={skipped}  "
        f"Failed={len(real_failures)}  NotReady={not_ready_count}",
        "",
    ]
    for kind in sorted(by_kind):
        details = by_kind[kind]
        lines.append(f"[{kind}] ({len(details)})")
        for d in details:
            lines.append(f"  - {d}")
        lines.append("")

    # Subject differs based on severity — lockout is catastrophic.
    # A NOT_READY-only run is informational; don't say "failed".
    if any(f["kind"] == "ACCOUNT_LOCKED" for f in _FAILURES):
        subject = "URGENT: Brisnet account locked"
    elif any(f["kind"] == "CRASH" for f in _FAILURES):
        subject = "Brisnet downloader crashed"
    elif any(f["kind"] == "LOGIN_FAILED" for f in _FAILURES):
        subject = "Brisnet login failed"
    elif not real_failures and not_ready_count:
        # Only NOT_READY — informational, not an alarm
        subject = f"Brisnet downloader: {not_ready_count} card(s) not yet posted"
    else:
        subject = f"Brisnet downloader: {len(real_failures)} file(s) failed"

    body = "\n".join(lines)
    try:
        _send_failure_email(subject, body)
        log.info(f"[notify] sent summary email: {subject}")
    except Exception as e:
        log.warning(f"[notify] could not send email: {e}")

# ── Config ───────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.resolve()
DRS_URL      = "https://www.brisnet.com/product/data-files/DRS"
LOGIN_URL    = "https://www.brisnet.com/product/login"
OUTPUT_DIR   = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads")
LOG_FILE     = BASE_DIR / "brisnet_download.log"
PRODUCT_CODE = "DRS"

# Chrome 136+ refuses to use the live profile directly. We copy it once,
# then point Selenium at the copy. Cookies/login state carry over.
CHROME_LIVE_PROFILE = r"C:\Users\ryanr\AppData\Local\Google\Chrome\User Data\Default"
CHROME_PROFILE_COPY = BASE_DIR / "chrome_selenium_profile"

# Load .env (BRISNET_USER, BRISNET_PASS)
# Read as UTF-8 explicitly. Without an explicit encoding, Python on Windows
# falls back to cp1252, which crashes on any UTF-8 byte sequence (smart
# quotes, em-dashes, euro signs, etc.) — and those slip into .env easily
# when the file is edited in Notepad or content gets pasted from a chat or
# document. We fall back to cp1252 with errors="replace" only if UTF-8
# decoding fails, so legacy ANSI .env files still load (with replacement
# characters for any non-ASCII bytes).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    try:
        _env_text = _env_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        _env_text = _env_file.read_text(encoding="cp1252", errors="replace")
    for _line in _env_text.splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Load DTS_TRACK_WHITELIST from config.py.  This is the same set used by
# run_pipeline.discover_drf_files() so the downloader and scorer agree on
# which tracks DTS cares about.  Falls back to None (download every track)
# if config.py is unavailable or doesn't define the symbol — keeps v3
# usable as a standalone script.
try:
    import sys as _sys
    if str(BASE_DIR) not in _sys.path:
        _sys.path.insert(0, str(BASE_DIR))
    import config as _config  # type: ignore
    DTS_TRACK_WHITELIST = getattr(_config, "DTS_TRACK_WHITELIST", None)
except Exception as _e:
    DTS_TRACK_WHITELIST = None
    print(f"[warn] could not load DTS_TRACK_WHITELIST from config.py ({_e}); "
          f"downloader will fetch every viewable track")
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ── Profile copy (Chrome 136+ workaround) ───────────────────────────────────

def ensure_profile_copy() -> Path:
    """
    Copy the live Chrome profile to a separate folder Selenium can use.
    Chrome 136+ refuses to use the live profile, so we mirror it once.
    On subsequent runs, the copy is reused.
    """
    src = Path(CHROME_LIVE_PROFILE)
    dst = CHROME_PROFILE_COPY / "Default"

    # Always rebuild the profile copy fresh. Reusing a prior copy can leave
    # Chrome lock / Preferences files in a state that triggers
    # "session not created: failed to write prefs file" on the next launch
    # (seen on machines where the live Chrome profile is sparse or rarely
    # used). The copy is small and fast, and the downloader logs in via
    # .env credentials each run anyway, so a fresh profile every run is the
    # reliable choice for unattended/scheduled operation.
    if CHROME_PROFILE_COPY.exists():
        log.info(f"[*] Removing stale profile copy at {CHROME_PROFILE_COPY}")
        shutil.rmtree(CHROME_PROFILE_COPY, ignore_errors=True)

    if not src.exists():
        raise FileNotFoundError(f"Live Chrome profile not found: {src}")

    log.info(f"[*] Copying Chrome profile (fresh each run)...")
    log.info(f"    From: {src}")
    log.info(f"    To:   {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Copy only essentials — keeps it small and fast.
    # Cookies + Login Data are what we actually need for auth carryover.
    for item in ["Cookies", "Cookies-journal", "Login Data", "Login Data-journal",
                 "Preferences", "Local State", "Network"]:
        s = src / item
        d = dst / item
        try:
            if s.is_file():
                shutil.copy2(s, d)
            elif s.is_dir():
                shutil.copytree(s, d, dirs_exist_ok=True)
        except Exception as e:
            log.warning(f"    skip {item}: {e}")

    # Local State actually lives at User Data root, not Default
    live_local_state = Path(CHROME_LIVE_PROFILE).parent / "Local State"
    if live_local_state.exists():
        try:
            shutil.copy2(live_local_state, CHROME_PROFILE_COPY / "Local State")
        except Exception as e:
            log.warning(f"    skip Local State: {e}")

    log.info("[*] Profile copy complete")
    return CHROME_PROFILE_COPY


# ── Selenium driver setup ───────────────────────────────────────────────────

def get_driver() -> webdriver.Chrome:
    profile_dir = ensure_profile_copy()

    # Make sure download dir exists before Chrome tries to use it
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    opts = Options()
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")

    # Point at the COPY of the profile (Chrome 136+ blocks the live one)
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--profile-directory=Default")

    # Anti-detection
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")

    # Chrome's built-in downloader: auto-save to OUTPUT_DIR, no prompts
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(OUTPUT_DIR.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "profile.default_content_settings.popups": 0,
    })

    driver = webdriver.Chrome(options=opts)

    # Strip the navigator.webdriver flag that gives Selenium away
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )

    # Force-allow downloads even in headless / automated mode (CDP)
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(OUTPUT_DIR.resolve()),
    })

    return driver


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_session_alive(driver) -> bool:
    """Quick probe to see if the Chrome session is still responsive."""
    try:
        _ = driver.window_handles
        return True
    except Exception:
        return False


def dismiss_cookie_banner(driver) -> None:
    """Best-effort dismiss of any cookie consent overlay."""
    try:
        for xp in (
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'got it')]",
            "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
        ):
            try:
                btn = driver.find_element(By.XPATH, xp)
                if btn.is_displayed():
                    btn.click()
                    time.sleep(1)
                    return
            except Exception:
                pass
    except Exception:
        pass


def is_logged_in(driver) -> bool:
    """Heuristic check: page text mentions logout/sign-out, not 'sign in'."""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return False
    if "logout" in body or "sign out" in body or "my account" in body:
        return True
    if "sign in" in body and "logout" not in body:
        return False
    # If url is the login page, definitely not
    return "login" not in driver.current_url.lower()


def wait_for_angular_data(driver, timeout: int = 30) -> None:
    """Wait until at least one .track.table-row has Angular scope data populated."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            count = driver.execute_script("""
                var rows = document.querySelectorAll('.track.table-row');
                var n = 0;
                for (var i = 0; i < rows.length; i++) {
                    var s = angular.element(rows[i]).scope();
                    if (s && s.track && s.track.trackCode) n++;
                }
                return n;
            """)
            if count and count > 0:
                log.info(f"[*] Angular data populated: {count} rows ready")
                return
        except Exception:
            pass
        time.sleep(1)
    log.warning("[!] Angular data did not populate within timeout")


def try_typed_login(driver, user: str, password: str) -> bool:
    """Type credentials into the login form. Returns True if login succeeded."""
    try:
        log.info(f"[*] Going to {LOGIN_URL}")
        driver.get(LOGIN_URL)
        time.sleep(2)
        dismiss_cookie_banner(driver)

        user_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//input[@type='text' or contains(@name,'user') or contains(@id,'user')]"
            ))
        )
        user_field.clear()
        user_field.send_keys(user)
        log.info("[*] Typed username")

        pass_field = driver.find_element(By.XPATH, "//input[@type='password']")
        pass_field.clear()
        pass_field.send_keys(password)
        log.info("[*] Typed password")

        login_btn = driver.find_element(
            By.XPATH,
            "//button[contains(translate(., 'LOGIN', 'login'), 'login')] | //input[@type='submit']"
        )
        login_btn.click()
        log.info("[*] Clicked login")

        WebDriverWait(driver, 15).until(lambda d: "login" not in d.current_url.lower())
        log.info(f"[*] Logged in, now at {driver.current_url}")
        return True
    except Exception as e:
        log.error(f"[!] Typed login failed: {e}")
        run_diagnostics(driver, suffix="_login_fail")

        # ── Detect account-state failures to elevate them in alerts ──
        # When typed login fails, we want to distinguish:
        #   - account locked  (URGENT — every retry extends the lockout)
        #   - bad credentials (still bad, but less catastrophic)
        #   - generic page failure (selectors changed, etc.)
        # The Brisnet lockout page contains phrases like "locked",
        # "excessive number", "reset password". We scan the page body
        # to classify.
        try:
            body_txt = (driver.page_source or "").lower()
        except Exception:
            body_txt = ""

        if "locked" in body_txt and ("excessive" in body_txt or "reset" in body_txt):
            _record_failure(
                "ACCOUNT_LOCKED",
                "Brisnet account is locked due to too many failed login "
                "attempts. Reset password at "
                "https://www.brisnet.com/product/reset and update .env "
                "(BRISNET_PASS) AND brisnet_login_helper.py before retrying.",
            )
        elif "invalid" in body_txt or "incorrect" in body_txt:
            _record_failure(
                "LOGIN_FAILED",
                f"Invalid credentials. Check BRISNET_USER and BRISNET_PASS "
                f"in .env. Last error: {e}",
            )
        else:
            _record_failure(
                "LOGIN_FAILED",
                f"Login form interaction failed (selectors may have "
                f"changed). Error: {e}",
            )
        return False


def run_diagnostics(driver, suffix: str = "") -> None:
    """Dump page state for debugging when things go wrong."""
    try:
        png = BASE_DIR / f"diag_screenshot{suffix}.png"
        driver.save_screenshot(str(png))
        log.info(f"[diag] screenshot -> {png}")

        html = BASE_DIR / f"diag_page{suffix}.html"
        html.write_text(driver.page_source, encoding="utf-8")
        log.info(f"[diag] page html  -> {html}")

        log.info(f"[diag] current url: {driver.current_url}")

        # Cookie names
        try:
            cookies = driver.get_cookies()
            names = sorted({c.get("name", "?") for c in cookies})
            log.info(f"[diag] cookies ({len(cookies)}): {names}")
        except Exception:
            pass

        # Body text fingerprint
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            sample = body[:500].replace("\n", " | ")
            log.info(f"[diag] body sample: {sample}")
        except Exception:
            pass

        # Track row count
        try:
            n = len(driver.find_elements(By.CSS_SELECTOR, "div.track.table-row"))
            log.info(f"[diag] .track.table-row count: {n}")
        except Exception:
            pass
    except Exception as e:
        log.error(f"[diag] diagnostics failed: {e}")


# ── Angular scope extraction ─────────────────────────────────────────────────

# Which productStatus values count as "downloadable", per product.
# DRS (past performances) is only usable once posts are drawn -> 'F' (FINAL).
# CCF (charts) is a RESULT product and never carries the DRS 'F' flag, so an
# 'F'-only filter silently discards every chart (seen: 84 skipped -> 0 tracks
# -> a misleading "session cannot drive the grid" abort). Empty list = accept all.
ACCEPT_STATUSES = {"DRS": ["F"]}


def extract_tracks(driver) -> list[dict]:
    """
    Pull all viewable tracks/dates for the configured product code.
    Filters:
      - productCode === PRODUCT_CODE  (e.g. 'DRS')
      - customerAvailability === 'View'  (you can download it)
      - productStatus in ACCEPT_STATUSES[PRODUCT_CODE] (DRS: 'F'; CCF: all)
    """
    js = """
    var rows = document.querySelectorAll('.track.table-row');
    var seen = {};
    var result = [];
    var skippedNonFinal = 0;
    var statusHist = {};
    for (var i = 0; i < rows.length; i++) {
        var scope = angular.element(rows[i]).scope();
        if (!scope || !scope.track) continue;
        var t = scope.track;
        if (seen[t.trackCode]) continue;
        seen[t.trackCode] = true;

        var dates = [];
        for (var j = 0; j < (t.availableDates || []).length; j++) {
            var d = t.availableDates[j];
            for (var k = 0; k < (d.availableProducts || []).length; k++) {
                var p = d.availableProducts[k];
                if (p.productCode === arguments[0] && p.customerAvailability === 'View') {
                    statusHist[p.productStatus] = (statusHist[p.productStatus] || 0) + 1;
                    var ok = arguments[1];
                    if (ok.length === 0 || ok.indexOf(p.productStatus) >= 0) {
                        dates.push({
                            productDate: d.productDate,
                            raceNumber: p.raceNumber || 0
                        });
                    } else {
                        skippedNonFinal++;
                    }
                }
            }
        }
        if (dates.length > 0) {
            result.push({
                trackCode:  t.trackCode,
                trackName:  t.trackName,
                country:    t.country,
                trackType:  t.trackType,
                dayEvening: t.dayEvening,
                dates:      dates
            });
        }
    }
    return JSON.stringify({tracks: result, skippedNonFinal: skippedNonFinal, statusHist: statusHist});
    """
    accept = ACCEPT_STATUSES.get(PRODUCT_CODE, [])
    raw = driver.execute_script(js, PRODUCT_CODE, accept)
    parsed = json.loads(raw)
    log.info(f"[*] {PRODUCT_CODE} productStatus seen: {parsed.get('statusHist')} "
             f"(accepting {accept or 'ALL'})")
    log.info(f"[*] Skipped {parsed['skippedNonFinal']} non-finalized files (no post positions yet)")
    return parsed["tracks"]


def _filter_to_whitelist(tracks: list[dict]) -> list[dict]:
    """
    Drop any track whose trackCode isn't in config.DTS_TRACK_WHITELIST.

    DTS_TRACK_WHITELIST is the set of tracks DTS actually scores and
    publishes — currently the top 30 North American thoroughbred tracks
    by daily handle, with both modern Equibase codes and legacy BRISnet
    aliases (e.g. CD/CDX, GP/GPX) so either form passes.

    If DTS_TRACK_WHITELIST is None (config unavailable, or explicitly set
    to None to disable filtering), this function is a no-op and every
    viewable track is returned. This makes the filter opt-in via config,
    matching how run_pipeline.discover_drf_files() treats it.
    """
    if DTS_TRACK_WHITELIST is None:
        log.info(
            f"[*] No track whitelist active — downloading all "
            f"{len(tracks)} viewable tracks"
        )
        return tracks

    kept, dropped = [], []
    for t in tracks:
        code = (t.get("trackCode") or "").upper()
        if code in DTS_TRACK_WHITELIST:
            kept.append(t)
        else:
            dropped.append(t)

    log.info(
        f"[*] Track whitelist applied: keeping {len(kept)}, "
        f"dropping {len(dropped)} non-DTS tracks"
    )
    if dropped:
        # Compact "code (name)" listing across one or two lines so the log
        # stays readable even on big race days.
        names = ", ".join(
            f"{(t.get('trackCode') or '?')} ({t.get('trackName') or '?'})"
            for t in dropped
        )
        log.info(f"    Dropped: {names}")
    if kept:
        names = ", ".join(
            f"{(t.get('trackCode') or '?')} ({t.get('trackName') or '?'})"
            for t in kept
        )
        log.info(f"    Kept:    {names}")
    return kept


def build_url(track: dict, date_iso: str, race_number: int = 0) -> str:
    """
    Construct the Brisnet download URL.
    Pattern (reverse-engineered from network traffic):
      /product/download/{YYYY-MM-DD}/{PRODUCT}/{COUNTRY}/{TRACKTYPE}/
                       {TRACKCODE}/{D|E}/{RACE_NUMBER}/
    """
    date_part = date_iso[:10]  # "2026-05-08"
    return (
        f"https://www.brisnet.com/product/download/"
        f"{date_part}/{PRODUCT_CODE}/{track['country']}/{track['trackType']}/"
        f"{track['trackCode']}/{track['dayEvening']}/{race_number}/"
    )


# ── Browser-driven download ──────────────────────────────────────────────────

def _list_partial_downloads() -> set[Path]:
    return {p for p in OUTPUT_DIR.glob("*.crdownload")} | \
           {p for p in OUTPUT_DIR.glob("*.tmp")}


def _sweep_stale_partials() -> None:
    """
    Delete any leftover .crdownload/.tmp partials before a run starts.

    A single stuck partial (e.g. left behind when a download returned an
    HTML error blob instead of a file) makes _wait_for_new_file() block on
    every subsequent download: it never returns while any partial exists,
    so each download silently times out and gets mislabeled NOT READY --
    even finalized cards that actually downloaded fine. Sweeping them at
    startup makes the unattended/scheduled run self-healing.
    """
    stale = _list_partial_downloads()
    if not stale:
        return
    removed = 0
    for p in stale:
        try:
            p.unlink()
            removed += 1
            log.info(f"[*] Swept stale partial: {p.name}")
        except Exception as e:
            log.warning(f"    could not remove stale partial {p.name}: {e}")
    log.info(f"[*] Cleared {removed} stale partial download(s) before run")


def _drf_identity(path: Path):
    """(track, YYYYMMDD) actually contained in a DRF, or (None, None)."""
    try:
        from ingest_drf import load_drf
        df = load_drf(path, "XXX", "0101", "2026", validate=False)
        t = df["Track"].dropna().astype(str).str.strip()
        t = t[t != ""]
        d = df["Date"].dropna()
        if t.empty or len(d) == 0:
            return None, None
        return t.mode().iloc[0].upper(), d.iloc[0].strftime("%Y%m%d")
    except Exception as e:
        log.warning(f"  could not read identity from {path.name}: {e}")
        return None, None


def _wait_for_new_file(before: set[Path], timeout: int = 30) -> Path | None:
    """Wait for a new (non-partial) file to appear in OUTPUT_DIR."""
    end = time.time() + timeout
    while time.time() < end:
        # No partial downloads in flight?
        if not _list_partial_downloads():
            now = {p for p in OUTPUT_DIR.iterdir() if p.is_file()}
            new_files = now - before
            new_files = {p for p in new_files
                         if not p.suffix.lower() in (".crdownload", ".tmp")}
            if new_files:
                # Newest one
                return max(new_files, key=lambda p: p.stat().st_mtime)
        time.sleep(0.5)
    return None


def download_via_browser(driver, url: str, timeout: int = 30) -> Path | None:
    """
    Trigger a download by having Chrome navigate to the URL in a new tab.
    Chrome auto-saves to OUTPUT_DIR. Returns the saved file path or None.
    Returns None if the session is dead — caller should detect and rebuild.
    """
    if not is_session_alive(driver):
        log.error("[!] Chrome session is dead before download attempt")
        return None

    before = {p for p in OUTPUT_DIR.iterdir() if p.is_file()}

    try:
        main_handle = driver.current_window_handle
    except Exception as e:
        log.error(f"[!] Cannot get window handle (session dead?): {e}")
        return None

    try:
        # New tab so we don't lose page state
        driver.execute_script(f"window.open('{url}', '_blank');")
        time.sleep(1)
        # Switch to the new tab so Chrome treats download as user-initiated
        for h in driver.window_handles:
            if h != main_handle:
                driver.switch_to.window(h)
                break

        new_file = _wait_for_new_file(before, timeout=timeout)

        # Close the download tab and switch back
        try:
            driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(main_handle)
        except Exception:
            log.warning("[!] Lost main window handle after download")

        return new_file
    except Exception as e:
        log.error(f"[!] download_via_browser error: {e}")
        try:
            if is_session_alive(driver):
                driver.switch_to.window(main_handle)
        except Exception:
            pass
        return None


def _verify_session_can_drive_grid(
    driver,
    user: str,
    password: str,
    context: str,
) -> bool:
    """
    After a Chrome restart, navigate to DRS and confirm the page is FULLY
    authenticated — not just "has cookies". `is_logged_in()` is too
    permissive: a session can carry valid cookies, render the page without
    redirecting to login, but still return an empty grid (zero track rows)
    because the back-end considers the session stale or partial. When that
    happens, every download attempt resolves to an error HTML page rather
    than a .zip, and Playwright/Selenium times out waiting for the file.

    The check: navigate to DRS_URL, wait for the Angular grid to populate,
    count track rows. If the count is > 0, the session is real. If it's 0,
    force a typed re-login (matching the startup-time recovery path), then
    re-verify. Returns True if the session is now usable, False if we
    couldn't recover.

    `context` is a short string used in log messages so the operator can
    tell whether the verify happened post-restart or post-session-death.
    """
    try:
        driver.get(DRS_URL)
        dismiss_cookie_banner(driver)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
        )
        wait_for_angular_data(driver, timeout=30)
        tracks = extract_tracks(driver)
        if tracks:
            log.info(
                f"[*] {context}: session verified ({len(tracks)} tracks "
                f"visible)"
            )
            return True
        log.warning(
            f"[!] {context}: 0 tracks after restart — session has cookies "
            f"but back-end won't serve data. Forcing typed re-login."
        )
    except TimeoutException:
        log.warning(
            f"[!] {context}: track grid never appeared after restart. "
            f"Forcing typed re-login."
        )
    except Exception as e:
        log.warning(f"[!] {context}: error checking grid ({e}). Forcing typed re-login.")

    # Fall through: typed re-login
    if not try_typed_login(driver, user, password):
        log.error(f"[!] {context}: typed re-login failed")
        return False
    try:
        driver.get(DRS_URL)
        dismiss_cookie_banner(driver)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
        )
        wait_for_angular_data(driver, timeout=30)
        tracks = extract_tracks(driver)
        if tracks:
            log.info(
                f"[*] {context}: re-login successful "
                f"({len(tracks)} tracks visible)"
            )
            return True
        log.error(f"[!] {context}: still 0 tracks after re-login")
        return False
    except Exception as e:
        log.error(f"[!] {context}: grid still empty after re-login ({e})")
        return False


# ── Main flow ────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info(f"  Brisnet DRF Downloader v3 — {datetime.now():%Y-%m-%d %H:%M}")
    log.info("=" * 60)

    # Reset failure list — each invocation gets a clean slate.
    _FAILURES.clear()

    # Local tallies; mutable list so the finally block can read final values.
    counts = {"ok": 0, "skipped": 0, "failed": 0}

    user = os.environ.get("BRISNET_USER", "")
    password = os.environ.get("BRISNET_PASS", "")
    if not user or not password:
        log.error("[!] BRISNET_USER and BRISNET_PASS must be set in .env")
        _record_failure(
            "CONFIG_ERROR",
            "BRISNET_USER and/or BRISNET_PASS not set in .env. "
            "Downloader cannot run.",
        )
        # Send the alert immediately; no driver to clean up.
        _send_run_summary_email(0, 0)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Clear any stuck partial downloads from a prior run so they don't
    # poison _wait_for_new_file() and cause false NOT_READY results.
    _sweep_stale_partials()

    driver = get_driver()
    try:
        # 1. Land on Brisnet root to set basic cookies
        driver.get("https://www.brisnet.com")
        time.sleep(2)
        dismiss_cookie_banner(driver)

        # 2. Try the data-files page directly. If session is alive, we'll see tracks.
        log.info(f"[*] Going to {DRS_URL}")
        driver.get(DRS_URL)
        time.sleep(3)
        dismiss_cookie_banner(driver)

        # 3. If not logged in, type credentials
        if not is_logged_in(driver):
            log.info("[*] Not logged in — using typed credentials")
            if not try_typed_login(driver, user, password):
                log.error("[!] Could not log in. Aborting.")
                run_diagnostics(driver, suffix="_login")
                # If try_typed_login already recorded LOGIN_FAILED or
                # ACCOUNT_LOCKED with full detail, don't duplicate.
                if not any(f["kind"] in ("LOGIN_FAILED", "ACCOUNT_LOCKED")
                           for f in _FAILURES):
                    _record_failure(
                        "LOGIN_FAILED",
                        "Initial login attempt failed. See logs for details.",
                    )
                return
            driver.get(DRS_URL)
            time.sleep(3)
            dismiss_cookie_banner(driver)
        else:
            log.info("[*] Already logged in via profile cookies")

        # 4. Wait for the AngularJS grid to render
        try:
            WebDriverWait(driver, 25).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
            )
        except TimeoutException:
            log.error("[!] .track.table-row never appeared")
            run_diagnostics(driver, suffix="_no_grid")
            _record_failure(
                "GRID_TIMEOUT",
                "Brisnet data files page loaded but the track grid never "
                "rendered within 25 seconds. Page structure may have "
                "changed, or there's a connectivity problem.",
            )
            return

        # Angular populates scope after the elements render; give it time
        wait_for_angular_data(driver, timeout=30)

        # 5. Extract tracks with viewable, FINAL DRS files
        tracks = extract_tracks(driver)
        log.info(f"[*] Found {len(tracks)} tracks with viewable, FINAL DRS files")
        tracks = _filter_to_whitelist(tracks)

        # If 0 tracks, we may be logged out. One retry via typed login.
        if not tracks:
            log.warning("[!] No viewable files — running diagnostics and retrying login")
            run_diagnostics(driver, suffix="_before_relogin")
            if try_typed_login(driver, user, password):
                log.info("[*] Re-login successful, retrying scope extraction")
                driver.get(DRS_URL)
                dismiss_cookie_banner(driver)
                try:
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
                    )
                    wait_for_angular_data(driver, timeout=30)
                    tracks = extract_tracks(driver)
                    log.info(f"[*] After re-login: found {len(tracks)} tracks")
                    tracks = _filter_to_whitelist(tracks)
                except TimeoutException:
                    log.error("[!] Track table never appeared after re-login")

            if not tracks:
                log.error("[!] Still no viewable files after re-login attempt")
                run_diagnostics(driver, suffix="_final")
                _record_failure(
                    "NO_TRACKS",
                    "Brisnet returned 0 viewable, FINAL DRS files even "
                    "after re-login. Either no cards are available today "
                    "or the scope extraction is broken (Brisnet may have "
                    "changed the page structure).",
                )
                return

        # 6. Download each file via the authenticated browser
        # Restart Chrome every N successful downloads to avoid resource leaks
        RESTART_AFTER_N = 15

        ok = skipped = failed = 0
        downloads_since_restart = 0

        for t in tracks:
            code = t["trackCode"]
            name = t["trackName"]
            for date_entry in t["dates"]:
                iso_date = date_entry["productDate"]
                race_num = date_entry.get("raceNumber", 0)
                date_str = iso_date[:10].replace("-", "")  # "20260508"
                target_filename = f"{date_str}_{code}_{PRODUCT_CODE}.DRF"
                target_path = OUTPUT_DIR / target_filename

                if target_path.exists():
                    log.info(f"  -> Skip (exists): {target_filename}")
                    skipped += 1
                    continue

                # Periodic Chrome restart to release accumulated resources
                if downloads_since_restart >= RESTART_AFTER_N:
                    log.info(f"[*] Restarting Chrome after {downloads_since_restart} downloads...")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = get_driver()
                    if not _verify_session_can_drive_grid(
                        driver, user, password,
                        context="post-restart",
                    ):
                        _record_failure(
                            "LOGIN_FAILED",
                            f"Could not re-authenticate after Chrome "
                            f"restart (after {downloads_since_restart} "
                            f"downloads). Run aborted.",
                        )
                        return
                    downloads_since_restart = 0

                # Detect dead session before attempting (defensive)
                if not is_session_alive(driver):
                    log.warning("[!] Chrome session died unexpectedly, rebuilding...")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = get_driver()
                    if not _verify_session_can_drive_grid(
                        driver, user, password,
                        context="post-session-death",
                    ):
                        _record_failure(
                            "LOGIN_FAILED",
                            "Chrome session died and could not "
                            "re-authenticate. Run aborted.",
                        )
                        return

                url = build_url(t, iso_date, race_num)
                log.info(f"[DL] {name} ({code}) {date_str}")
                log.info(f"     {url}")

                downloaded_file = download_via_browser(driver, url)
                if downloaded_file:
                    # Brisnet serves an HTML page (Chrome saves it as
                    # downloads.htm) instead of a .DRF when a card isn't
                    # posted yet. That's NOT_READY (expected for future
                    # dates), not a rename failure -- detect the HTML blob,
                    # delete it so it can't poison later runs, and skip.
                    if downloaded_file.suffix.lower() in (".htm", ".html"):
                        try:
                            downloaded_file.unlink()
                        except Exception:
                            pass
                        from datetime import date as _date
                        try:
                            race_dt = _date(int(date_str[:4]),
                                            int(date_str[4:6]),
                                            int(date_str[6:8]))
                            days_out = (race_dt - _date.today()).days
                        except Exception:
                            days_out = 0
                        log.info(
                            f"  NOT READY (HTML page, not a DRF; race in "
                            f"{days_out}d): {name} ({code}) {date_str} "
                            f"— Brisnet likely hasn't posted this card yet"
                        )
                        failed += 1
                        _record_failure(
                            "NOT_READY",
                            f"{name} ({code}) {date_str}: Brisnet returned an "
                            f"HTML page instead of a DRF (card not yet "
                            f"available, race in {days_out} days). URL: {url}",
                        )
                        continue
                    # A slow download lands during the NEXT request's wait
                    # window, so _wait_for_new_file can hand back a file we
                    # didn't ask for. Trust the CONTENTS, never the request:
                    # park the file under its true name and report the
                    # requested card as not obtained so it gets retried.
                    real_track, real_date = _drf_identity(downloaded_file)
                    if real_track and real_date and not (
                        real_track in (code.upper(), code.upper()[:2],
                                       code.upper().rstrip("X"))
                        and real_date == date_str
                    ):
                        true_name = f"{real_date}_{real_track}_{PRODUCT_CODE}.DRF"
                        true_path = OUTPUT_DIR / true_name
                        log.warning(
                            f"  CONTENT MISMATCH: asked for {code} {date_str}, "
                            f"got {real_track} {real_date}. Saving as {true_name} "
                            f"(NOT {target_filename})."
                        )
                        try:
                            if true_path.exists():
                                true_path.unlink()
                            downloaded_file.rename(true_path)
                        except Exception as e:
                            log.error(f"  could not park mismatched file: {e}")
                        failed += 1
                        _record_failure(
                            "CONTENT_MISMATCH",
                            f"{name} ({code}) {date_str}: download returned "
                            f"{real_track} {real_date} instead. Saved as "
                            f"{true_name}; {code} {date_str} still needed.",
                        )
                        continue

                    try:
                        if downloaded_file != target_path:
                            if target_path.exists():
                                target_path.unlink()
                            downloaded_file.rename(target_path)
                        log.info(f"  OK {target_path.name} ({target_path.stat().st_size:,} bytes)")
                        ok += 1
                        downloads_since_restart += 1
                    except Exception as e:
                        log.error(f"  FAIL rename: {e}")
                        failed += 1
                        _record_failure(
                            "RENAME_FAILED",
                            f"{name} ({code}) {date_str}: could not rename "
                            f"downloaded file to {target_filename}. Error: {e}",
                        )
                else:
                    # Distinguish "Brisnet doesn't have this card yet"
                    # (NOT_READY, expected for distant-future dates) from
                    # "the download genuinely failed" (DOWNLOAD_TIMEOUT,
                    # worth alerting on). Brisnet typically posts PPs
                    # ~24-36 hours ahead, so failures on race dates 2+
                    # days out are usually "not ready yet" rather than
                    # broken downloads.
                    from datetime import date as _date
                    try:
                        race_dt = _date(
                            int(date_str[:4]),
                            int(date_str[4:6]),
                            int(date_str[6:8]),
                        )
                        days_out = (race_dt - _date.today()).days
                    except Exception:
                        days_out = 0

                    if days_out >= 2:
                        log.info(
                            f"  NOT READY (race in {days_out}d): {url} "
                            f"— Brisnet likely hasn't posted this card yet"
                        )
                        failed += 1
                        _record_failure(
                            "NOT_READY",
                            f"{name} ({code}) {date_str}: card not yet "
                            f"available on Brisnet (race in {days_out} "
                            f"days). URL: {url}",
                        )
                    else:
                        log.warning(f"  FAIL no file appeared for {url}")
                        failed += 1
                        _record_failure(
                            "DOWNLOAD_TIMEOUT",
                            f"{name} ({code}) {date_str}: no file appeared "
                            f"after timeout. URL: {url}",
                        )
                    # If session is now dead, the next iteration will rebuild it

        log.info("=" * 60)
        log.info(f"  Downloaded: {ok}  Skipped: {skipped}  Failed: {failed}")
        log.info(f"  Output: {OUTPUT_DIR}")
        log.info("=" * 60)

        # Hand the tallies to the finally block so the email summary is
        # accurate even if a later exception interrupts the cleanup path.
        counts["ok"] = ok
        counts["skipped"] = skipped
        counts["failed"] = failed

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        # Send summary email LAST — after driver cleanup, never before, so
        # we don't leave a zombie Chrome process if SMTP hangs.
        _send_run_summary_email(counts["ok"], counts["skipped"])


if __name__ == "__main__":
    # Top-level wrapper: any uncaught exception (chromedriver crash,
    # SessionNotCreated at startup, KeyError on a malformed scope, etc.)
    # gets recorded and emailed before the process exits with a non-zero
    # status. Without this wrapper the scheduled task fails silently.
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        # Operator-initiated. Don't email — they know what they did.
        log.warning("[!] Interrupted by user (Ctrl+C)")
        raise
    except Exception as _crash:
        import traceback
        tb = traceback.format_exc()
        log.error(f"[!] Unhandled exception in main(): {_crash}")
        log.error(tb)
        _record_failure(
            "CRASH",
            f"Unhandled exception: {type(_crash).__name__}: {_crash}\n\n"
            f"Traceback (most recent call last):\n{tb}",
        )
        _send_run_summary_email(0, 0)
        raise SystemExit(1)
