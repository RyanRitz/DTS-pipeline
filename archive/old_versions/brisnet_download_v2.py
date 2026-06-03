"""
brisnet_download_v2.py
======================
Extracts track data from Angular scope via .track.table-row selector,
then downloads all DRS files where customerAvailability == "View".
"""

import json
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

# ── Config ───────────────────────────────────────────────────────────────────
BRISNET_URL  = "https://www.brisnet.com/product/data-files/DRS"
OUTPUT_DIR   = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads")
COOKIE_FILE  = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\brisnet_cookies.json")
LOG_FILE     = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\brisnet_download.log")
PRODUCT_CODE = "DRS"
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)


def get_driver():
    opts = Options()
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")

    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    return webdriver.Chrome(options=opts)


def load_cookies(driver, path: Path):
    if not path.exists():
        log.warning("No cookie file found — may not be authenticated")
        return
    with open(path) as f:
        for c in json.load(f):
            try: driver.add_cookie(c)
            except: pass
    log.info(f"Loaded cookies from {path}")


def extract_tracks(driver) -> list[dict]:
    """Pull all track data from the Angular child scope on .track.table-row."""
    js = """
    var rows = document.querySelectorAll('.track.table-row');
    var seen = {};
    var result = [];
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
                    dates.push(d.productDate);
                }
            }
        }
        if (dates.length > 0) {
            result.push({trackCode: t.trackCode, trackName: t.trackName, dates: dates});
        }
    }
    return JSON.stringify(result);
    """
    raw = driver.execute_script(js, PRODUCT_CODE)
    return json.loads(raw)


def build_url(track_code: str, date_str: str) -> str:
    return (
        f"https://www.brisnet.com/cgi-bin/card.cgi"
        f"?func=download&sdate={date_str}&track={track_code}&product={PRODUCT_CODE}"
    )


def make_session(driver) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = driver.execute_script("return navigator.userAgent")
    s.headers["Referer"] = BRISNET_URL
    for c in driver.get_cookies():
        s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return s


def download(session: requests.Session, url: str, dest: Path) -> bool:
    try:
        r = session.get(url, stream=True, timeout=30)
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            log.warning(f"  Got HTML (not a file) — auth issue? {url}")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        log.info(f"  ✓ {dest.name}  ({dest.stat().st_size:,} bytes)")
        return True
    except Exception as e:
        log.error(f"  ✗ {e}")
        return False


def main():
    log.info("=" * 60)
    log.info(f"  Brisnet DRF Downloader v2 — {datetime.now():%Y-%m-%d %H:%M}")
    log.info("=" * 60)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    driver = get_driver()
    try:
        # Navigate to login page and wait for user to log in manually
        driver.get("https://www.brisnet.com/product/data-files/DRS")
        log.info("[*] Browser opened — please log in if needed, then press Enter here...")
        input("    [Press Enter once you are logged in and the page has loaded] ")

        # Save fresh cookies for future runs
        cookies = driver.get_cookies()
        with open(COOKIE_FILE, "w") as f:
            json.dump(cookies, f, indent=2)
        log.info(f"[*] Saved {len(cookies)} fresh cookies to {COOKIE_FILE}")

        log.info("[*] Waiting for Angular to render...")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
        )

        # Poll until Angular scope actually has track data (up to 30s)
        log.info("[*] Waiting for Angular scope data to populate...")
        for attempt in range(30):
            count = driver.execute_script("""
                var rows = document.querySelectorAll('.track.table-row');
                for (var i = 0; i < rows.length; i++) {
                    var scope = angular.element(rows[i]).scope();
                    if (scope && scope.track && scope.track.availableDates && scope.track.availableDates.length > 0)
                        return scope.track.availableDates.length;
                }
                return 0;
            """)
            if count:
                log.info(f"[*] Angular data ready (attempt {attempt+1})")
                break
            time.sleep(1)
        else:
            log.warning("[!] Angular data never populated — proceeding anyway")

        # Debug: dump first track's raw availability data
        debug = driver.execute_script("""
            var rows = document.querySelectorAll('.track.table-row');
            var scope = angular.element(rows[0]).scope();
            var t = scope.track;
            var out = {trackCode: t.trackCode, trackName: t.trackName, dates: []};
            for (var j = 0; j < (t.availableDates || []).length; j++) {
                var d = t.availableDates[j];
                var prods = [];
                for (var k = 0; k < (d.availableProducts || []).length; k++) {
                    var p = d.availableProducts[k];
                    prods.push({code: p.productCode, avail: p.customerAvailability});
                }
                out.dates.push({date: d.productDate, products: prods});
            }
            return JSON.stringify(out);
        """)
        log.info(f"[DEBUG] First track raw data: {debug}")

        # Extract track/date data from Angular scope
        tracks = extract_tracks(driver)
        log.info(f"[*] Found {len(tracks)} tracks with viewable DRS files")

        session = make_session(driver)

    finally:
        driver.quit()

    # Download
    ok = skipped = failed = 0
    for t in tracks:
        code = t["trackCode"]
        name = t["trackName"]
        for iso_date in t["dates"]:
            date_str = iso_date[:10].replace("-", "")  # "20260506"
            filename = f"{date_str}_{code}_{PRODUCT_CODE}.DRF"
            dest = OUTPUT_DIR / date_str / filename

            if dest.exists():
                log.info(f"  → Skip (exists): {filename}")
                skipped += 1
                continue

            url = build_url(code, date_str)
            log.info(f"[↓] {name} ({code}) {date_str}")
            if download(session, url, dest):
                ok += 1
            else:
                failed += 1

    log.info("=" * 60)
    log.info(f"  Downloaded: {ok}  Skipped: {skipped}  Failed: {failed}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
