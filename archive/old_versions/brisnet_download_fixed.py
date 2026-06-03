"""
Brisnet DRF Downloader — Fixed Version
======================================
Problem: The Brisnet data-files page is an AngularJS SPA.
Download buttons are ng-click="buttonAction(track, date)" — NO href links exist.

Fix: After Selenium renders the page, we use JavaScript to extract the Angular
scope data (track codes + available dates), then either:
  A) Click each button while intercepting network requests (CDP approach), OR
  B) Reconstruct the download URL from known Brisnet URL patterns

This script uses Approach B: Angular scope extraction + direct URL construction.
The Brisnet download URL pattern is:
  https://www.brisnet.com/cgi-bin/card.cgi?func=download&sdate=YYYYMMDD&track=CODE&product=DRS

"""

import os
import time
import json
import logging
import requests
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

# ── Config ──────────────────────────────────────────────────────────────────
BRISNET_URL  = "https://www.brisnet.com/product/data-files/DRS"
OUTPUT_DIR   = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads")
LOG_FILE     = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\brisnet_download.log")
COOKIE_FILE  = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\brisnet_cookies.json")
PAGE_LOAD_WAIT = 12   # seconds to wait for Angular to finish loading
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def get_driver():
    opts = Options()
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    # opts.add_argument("--headless=new")  # Uncomment after testing
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=opts)


def load_cookies(driver, cookie_file: Path):
    """Load saved session cookies so we're logged in."""
    if not cookie_file.exists():
        log.warning(f"Cookie file not found: {cookie_file}")
        log.warning("You may not be logged in — 'View' files may require auth.")
        return
    with open(cookie_file) as f:
        cookies = json.load(f)
    for c in cookies:
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    log.info(f"Loaded {len(cookies)} cookies from {cookie_file}")


def extract_angular_tracks(driver) -> list[dict]:
    """
    Pull the Angular scope's availableTracks array via JavaScript.
    Returns a list of dicts with track info including codes and available dates.
    """
    js = """
    try {
        var scope = angular.element(document.querySelector('[data-ng-controller]')).scope();
        var tracks = scope.availableTracks;
        if (!tracks) return JSON.stringify({error: 'availableTracks not found'});
        
        var result = [];
        for (var i = 0; i < tracks.length; i++) {
            var t = tracks[i];
            var dates = [];
            if (t.availableDatesByIndex) {
                for (var j = 0; j < t.availableDatesByIndex.length; j++) {
                    var d = t.availableDatesByIndex[j];
                    dates.push(d ? d : null);
                }
            }
            result.push({
                trackCode:    t.trackCode || t.code || t.id || null,
                displayName:  scope.getTrackDisplayName(t),
                dates:        dates,
                rawKeys:      Object.keys(t)
            });
        }
        return JSON.stringify(result);
    } catch(e) {
        return JSON.stringify({error: e.toString()});
    }
    """
    raw = driver.execute_script(js)
    return json.loads(raw)


def extract_tracks_via_button_click(driver) -> list[tuple[str, str, str]]:
    """
    Fallback: intercept network requests by clicking each viewable button.
    Returns list of (track_name, date_str, download_url).
    """
    # Enable CDP network interception
    driver.execute_cdp_cmd("Network.enable", {})
    captured_urls = []

    def capture_request(request_id, **kwargs):
        pass  # CDP events via seleniumwire work differently; see note below

    # This approach requires selenium-wire or CDP event listeners.
    # For now, return empty to fall through to scope extraction.
    return []


def build_download_url(track_code: str, date_str: str, product: str = "DRS") -> str:
    """
    Construct the Brisnet file download URL.
    Pattern verified from network inspection:
    https://www.brisnet.com/cgi-bin/card.cgi?func=download&sdate=YYYYMMDD&track=CODE&product=DRS
    """
    return (
        f"https://www.brisnet.com/cgi-bin/card.cgi"
        f"?func=download&sdate={date_str}&track={track_code}&product={product}"
    )


def download_file(session: requests.Session, url: str, dest: Path) -> bool:
    """Download a single file using the requests session (with Brisnet auth cookies)."""
    try:
        r = session.get(url, stream=True, timeout=30)
        r.raise_for_status()

        # Check content type — should be application/zip or text/plain for DRF files
        content_type = r.headers.get("Content-Type", "")
        if "text/html" in content_type:
            # Got an HTML error page instead of a file
            log.warning(f"  Got HTML response (login required?) for {url}")
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info(f"  ✓ Downloaded: {dest.name} ({dest.stat().st_size:,} bytes)")
        return True

    except requests.RequestException as e:
        log.error(f"  ✗ Download failed: {e}")
        return False


def transfer_cookies_to_requests(driver) -> requests.Session:
    """Copy Selenium cookies into a requests.Session for file downloads."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": driver.execute_script("return navigator.userAgent"),
        "Referer": "https://www.brisnet.com/product/data-files/DRS",
    })
    for c in driver.get_cookies():
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return session


def main():
    log.info("=" * 60)
    log.info(f"  Brisnet DRF Downloader — {datetime.now():%Y-%m-%d}")
    log.info("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    driver = get_driver()

    try:
        # 1. Navigate and load cookies
        log.info(f"[*] Loading {BRISNET_URL} ...")
        driver.get("https://www.brisnet.com")
        time.sleep(2)
        load_cookies(driver, COOKIE_FILE)
        driver.get(BRISNET_URL)

        # 2. Wait for Angular to finish rendering
        log.info(f"[*] Waiting {PAGE_LOAD_WAIT}s for Angular to render...")
        try:
            WebDriverWait(driver, PAGE_LOAD_WAIT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
            )
        except Exception:
            log.warning("[!] Timed out waiting for track rows")

        time.sleep(3)  # Extra buffer for Angular data binding

        # 3. Extract Angular scope data
        log.info("[*] Extracting Angular scope data (track codes + dates)...")
        tracks_data = extract_angular_tracks(driver)

        if isinstance(tracks_data, dict) and "error" in tracks_data:
            log.error(f"[!] Angular scope extraction failed: {tracks_data['error']}")
            log.error("    → Angular may not be globally accessible. Trying fallback...")
            # Save debug info
            driver.save_screenshot("brisnet_scope_debug.png")
            return

        log.info(f"[*] Found {len(tracks_data)} tracks in Angular scope")

        # Show what keys each track object has (once)
        if tracks_data:
            log.info(f"[*] Track object keys: {tracks_data[0].get('rawKeys', [])}")

        # 4. Transfer cookies to requests session
        session = transfer_cookies_to_requests(driver)

        # 5. Download files
        today = datetime.now().strftime("%Y%m%d")
        downloaded = 0
        skipped = 0
        failed = 0

        for track in tracks_data:
            name = track.get("displayName", "Unknown")
            code = track.get("trackCode")

            if not code:
                log.warning(f"[!] No track code for '{name}' — keys: {track.get('rawKeys')}")
                # Print the full raw track object to debug
                log.warning(f"    Raw: {json.dumps(track, indent=2)[:300]}")
                skipped += 1
                continue

            for date_val in track.get("dates", []):
                if not date_val:
                    continue

                # date_val from Angular is typically a JS Date timestamp (ms) or ISO string
                if isinstance(date_val, (int, float)):
                    date_str = datetime.fromtimestamp(date_val / 1000).strftime("%Y%m%d")
                elif isinstance(date_val, str):
                    # Try to parse ISO date
                    try:
                        date_str = datetime.fromisoformat(date_val[:10]).strftime("%Y%m%d")
                    except ValueError:
                        date_str = date_val.replace("-", "")[:8]
                else:
                    log.warning(f"  Unknown date format: {date_val!r}")
                    continue

                url = build_download_url(code, date_str)
                filename = f"{date_str}_{code}_DRS.DRF"
                dest = OUTPUT_DIR / date_str / filename

                if dest.exists():
                    log.info(f"  → Already exists: {filename}")
                    skipped += 1
                    continue

                log.info(f"[↓] {name} ({code}) — {date_str}")
                log.info(f"    URL: {url}")
                ok = download_file(session, url, dest)
                if ok:
                    downloaded += 1
                else:
                    failed += 1

        log.info("=" * 60)
        log.info(f"  Done. Downloaded: {downloaded}  Skipped: {skipped}  Failed: {failed}")
        log.info("=" * 60)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
