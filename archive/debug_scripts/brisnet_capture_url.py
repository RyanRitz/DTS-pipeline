"""
brisnet_capture_url.py
======================
Diagnostic: opens the data files page, finds the FIRST viewable DRS file,
intercepts all network requests, then programmatically triggers a click on
that file's download icon. Captures and logs the actual download URL.

Run this once. The captured URL pattern will tell us exactly how to
reconstruct download URLs for all tracks/dates.
"""

import json
import time
import shutil
import logging
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

BASE_DIR = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation")
DRS_URL  = "https://www.brisnet.com/product/data-files/DRS"
PROFILE  = BASE_DIR / "chrome_selenium_profile"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "capture_url.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def get_driver():
    opts = Options()
    opts.add_argument(f"--user-data-dir={PROFILE}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=opts)


def main():
    log.info("=" * 60)
    log.info("  Brisnet URL Capture Diagnostic")
    log.info("=" * 60)

    driver = get_driver()

    try:
        driver.get(DRS_URL)
        log.info("[*] Page loaded, waiting for tracks to populate...")

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
        )

        # Wait for Angular data
        for _ in range(30):
            count = driver.execute_script("""
                var rows = document.querySelectorAll('.track.table-row');
                for (var i = 0; i < rows.length; i++) {
                    var s = angular.element(rows[i]).scope();
                    if (s && s.track && s.track.availableDates && s.track.availableDates.length > 0)
                        return s.track.availableDates.length;
                }
                return 0;
            """)
            if count: break
            time.sleep(1)

        # Inject XHR + fetch + window.open monkey-patches BEFORE clicking
        log.info("[*] Installing network interceptors...")
        driver.execute_script("""
            window.__captured = [];

            // XMLHttpRequest
            var origOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url) {
                window.__captured.push({type: 'XHR', method: method, url: String(url)});
                return origOpen.apply(this, arguments);
            };

            // fetch
            var origFetch = window.fetch;
            window.fetch = function(url, opts) {
                var u = (typeof url === 'string') ? url : (url && url.url) || String(url);
                window.__captured.push({type: 'FETCH', method: (opts && opts.method) || 'GET', url: u});
                return origFetch.apply(this, arguments);
            };

            // window.open (sometimes used to trigger downloads)
            var origOpenWin = window.open;
            window.open = function(url, target, features) {
                window.__captured.push({type: 'WINDOW_OPEN', method: 'GET', url: String(url)});
                return origOpenWin.apply(this, arguments);
            };

            // Monkey-patch document.location and form submits
            window.__origAssign = window.location.assign;
            // Note: we can't easily intercept location.href= but we'll catch most patterns
        """)

        # Find the first viewable DRS button via the Angular scope
        log.info("[*] Locating first viewable DRS file...")
        target = driver.execute_script("""
            var rows = document.querySelectorAll('.track.table-row');
            for (var i = 0; i < rows.length; i++) {
                var scope = angular.element(rows[i]).scope();
                if (!scope || !scope.track) continue;
                var t = scope.track;

                for (var j = 0; j < (t.availableDates || []).length; j++) {
                    var d = t.availableDates[j];
                    for (var k = 0; k < (d.availableProducts || []).length; k++) {
                        var p = d.availableProducts[k];
                        if (p.productCode === 'DRS' && p.customerAvailability === 'View') {
                            // Find the corresponding clickable cell in the DOM
                            var cells = rows[i].querySelectorAll('div[data-ng-repeat*="availableDatesByIndex"]');
                            // Index in availableDatesByIndex matches column position
                            var idx = -1;
                            for (var x = 0; x < t.availableDatesByIndex.length; x++) {
                                if (t.availableDatesByIndex[x] === d.productDate) { idx = x; break; }
                            }
                            return {
                                trackCode: t.trackCode,
                                trackName: t.trackName,
                                productDate: d.productDate,
                                cellIndex: idx,
                                rowIndex: i,
                                cellCount: cells.length
                            };
                        }
                    }
                }
            }
            return null;
        """)

        if not target:
            log.error("[!] No viewable DRS files found in scope — cannot test")
            return

        log.info(f"[*] Target: {json.dumps(target, indent=2)}")

        # Trigger buttonAction directly via Angular scope
        log.info("[*] Calling buttonAction(track, date) on scope...")
        result = driver.execute_script("""
            var rows = document.querySelectorAll('.track.table-row');
            var row = rows[arguments[0]];
            var scope = angular.element(row).scope();
            try {
                scope.buttonAction(scope.track, arguments[1]);
                return {ok: true};
            } catch(e) {
                return {ok: false, error: e.toString()};
            }
        """, target["rowIndex"], target["productDate"])

        log.info(f"[*] buttonAction result: {result}")

        # Wait a beat for any async requests to fire
        log.info("[*] Waiting 5s for network requests to fire...")
        time.sleep(5)

        # Pull captured requests
        captured = driver.execute_script("return window.__captured;")
        log.info(f"[*] Captured {len(captured)} network requests:")
        for r in captured:
            log.info(f"    {r['type']:12s} {r['method']:6s} {r['url']}")

        # Save them
        with open(BASE_DIR / "captured_requests.json", "w", encoding="utf-8") as f:
            json.dump(captured, f, indent=2)
        log.info(f"[*] Saved to captured_requests.json")

        # Also: get the current URL in case buttonAction navigated us somewhere
        log.info(f"[*] Current URL after click: {driver.current_url}")

        # And dump the page in case it's a download landing page
        with open(BASE_DIR / "after_click.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log.info(f"[*] Saved page after click to after_click.html")

    finally:
        time.sleep(2)
        driver.quit()


if __name__ == "__main__":
    main()
