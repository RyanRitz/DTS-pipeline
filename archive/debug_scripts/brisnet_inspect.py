"""
brisnet_inspect.py
===================
Throwaway diagnostic. Opens Brisnet, dumps the FIRST track object's
full structure to brisnet_track_dump.json, and exits.

Usage:
    python brisnet_inspect.py

Then send the contents of brisnet_track_dump.json back to Claude.
"""
import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

BASE_DIR     = Path(__file__).parent
COOKIES_FILE = BASE_DIR / "brisnet_cookies.json"
DUMP_FILE    = BASE_DIR / "brisnet_track_dump.json"
DRF_PAGE_URL = "https://www.brisnet.com/product/data-files/DRS"

JS_DUMP = r"""
var rows = document.querySelectorAll('div.track.table-row');
if (rows.length === 0) return JSON.stringify({error: 'no track rows in DOM'});
var scope = angular.element(rows[0]).scope();
if (!scope || !scope.availableTracks) {
    return JSON.stringify({error: 'no availableTracks in scope'});
}
// Dump the first 3 tracks fully, plus the field names of the first one
var first = scope.availableTracks[0];
var keys = Object.keys(first);
return JSON.stringify({
    total_tracks: scope.availableTracks.length,
    first_track_keys: keys,
    first_three_tracks: scope.availableTracks.slice(0, 3)
}, null, 2);
"""

def main():
    cookies = json.loads(COOKIES_FILE.read_text())

    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")

    print("[*] Launching Chrome...")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                              options=opts)
    try:
        driver.get("https://www.brisnet.com/")
        for ck in cookies:
            try:
                driver.add_cookie({k: ck[k] for k in
                                   ("name", "value", "domain", "path") if k in ck})
            except Exception:
                pass

        print(f"[*] Loading {DRF_PAGE_URL} ...")
        driver.get(DRF_PAGE_URL)

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
        )
        time.sleep(2)

        print("[*] Reading Angular scope...")
        result = driver.execute_script(JS_DUMP)
        DUMP_FILE.write_text(result, encoding="utf-8")
        print(f"[OK] Dumped to: {DUMP_FILE}")
        print()
        print("Send the contents of that file back to Claude.")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
