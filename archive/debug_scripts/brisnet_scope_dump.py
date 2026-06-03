"""
brisnet_scope_dump.py — Quick diagnostic
=========================================
Loads the Brisnet data-files page and dumps the Angular scope's
availableTracks to JSON so you can inspect the real track object structure
(specifically what field holds the track code and date values).

Run this FIRST to learn the field names, then update brisnet_download_fixed.py.
"""

import json
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

BRISNET_URL = "https://www.brisnet.com/product/data-files/DRS"
COOKIE_FILE = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\brisnet_cookies.json")
DUMP_FILE   = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\scope_dump.json")

opts = Options()
opts.add_experimental_option("excludeSwitches", ["enable-automation"])
driver = webdriver.Chrome(options=opts)

try:
    # Load site first so we can add cookies
    driver.get("https://www.brisnet.com")
    time.sleep(2)

    if COOKIE_FILE.exists():
        with open(COOKIE_FILE) as f:
            for c in json.load(f):
                try: driver.add_cookie(c)
                except: pass
        print(f"[*] Loaded cookies from {COOKIE_FILE}")

    driver.get(BRISNET_URL)
    print("[*] Waiting for Angular to render...")
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
    )
    time.sleep(4)

    # Dump Angular scope
    js = """
    try {
        var scope = angular.element(
            document.querySelector('[data-ng-controller]')
        ).scope();
        
        var tracks = scope.availableTracks;
        if (!tracks || !tracks.length) {
            return JSON.stringify({error: 'availableTracks empty or missing'});
        }
        
        // Return first 3 tracks in full detail
        var out = [];
        for (var i = 0; i < Math.min(3, tracks.length); i++) {
            var t = tracks[i];
            var plain = {};
            for (var k in t) {
                if (typeof t[k] !== 'function') {
                    try {
                        plain[k] = JSON.parse(JSON.stringify(t[k]));
                    } catch(e) {
                        plain[k] = String(t[k]);
                    }
                }
            }
            plain['__displayName'] = scope.getTrackDisplayName(t);
            out.push(plain);
        }
        return JSON.stringify({
            totalTracks: tracks.length,
            sample: out
        });
    } catch(e) {
        return JSON.stringify({error: e.toString()});
    }
    """

    raw = driver.execute_script(js)
    data = json.loads(raw)

    print(f"\n[*] Angular scope dump:")
    print(json.dumps(data, indent=2, default=str))

    with open(DUMP_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n[*] Full dump saved to: {DUMP_FILE}")

    input("\n[Press Enter to close browser]")

finally:
    driver.quit()
