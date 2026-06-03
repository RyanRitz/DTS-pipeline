"""
brisnet_scope_dump2.py — tries multiple Angular scope extraction approaches
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
DUMP_FILE   = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\scope_dump2.json")

opts = Options()
opts.add_experimental_option("excludeSwitches", ["enable-automation"])
driver = webdriver.Chrome(options=opts)

try:
    driver.get("https://www.brisnet.com")
    time.sleep(2)

    if COOKIE_FILE.exists():
        with open(COOKIE_FILE) as f:
            for c in json.load(f):
                try: driver.add_cookie(c)
                except: pass
        print("[*] Cookies loaded")

    driver.get(BRISNET_URL)
    print("[*] Waiting for track rows...")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row"))
    )
    time.sleep(6)  # Give Angular extra time to bind data
    print("[*] Page ready — running JS probes...")

    # Try several different selectors to find a scope with availableTracks
    js = """
    var selectors = [
        '[data-ng-controller]',
        '[ng-controller]',
        'body',
        '.track.table-row',
        '[data-ng-repeat="track in availableTracks"]'
    ];

    var results = {};

    for (var i = 0; i < selectors.length; i++) {
        var sel = selectors[i];
        var el = document.querySelector(sel);
        if (!el) { results[sel] = 'element not found'; continue; }
        try {
            var scope = angular.element(el).scope();
            if (!scope) { results[sel] = 'no scope'; continue; }
            var keys = Object.keys(scope).filter(function(k){ return k[0] !== '$'; });
            results[sel] = {
                keys: keys,
                hasAvailableTracks: !!scope.availableTracks,
                availableTracksLen: scope.availableTracks ? scope.availableTracks.length : 0
            };
            // If we found tracks, dump first one
            if (scope.availableTracks && scope.availableTracks.length > 0) {
                var t = scope.availableTracks[0];
                var plain = {};
                for (var k in t) {
                    if (typeof t[k] !== 'function' && k[0] !== '$') {
                        try { plain[k] = JSON.parse(JSON.stringify(t[k])); }
                        catch(e) { plain[k] = typeof t[k]; }
                    }
                }
                results[sel].firstTrack = plain;
                results[sel].displayName = scope.getTrackDisplayName ? scope.getTrackDisplayName(t) : 'N/A';
            }
        } catch(e) {
            results[sel] = 'error: ' + e.toString();
        }
    }

    // Also try injector approach
    try {
        var $rootScope = angular.element(document.body).injector().get('$rootScope');
        results['$rootScope_keys'] = Object.keys($rootScope).filter(function(k){ return k[0] !== '$'; });
    } catch(e) {
        results['$rootScope'] = 'error: ' + e.toString();
    }

    return JSON.stringify(results, null, 2);
    """

    raw = driver.execute_script(js)
    data = json.loads(raw)
    print(json.dumps(data, indent=2))

    with open(DUMP_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n[*] Saved to {DUMP_FILE}")

    # Also capture network requests to find the actual API
    print("\n[*] Now intercepting — click a download icon in the browser...")
    print("    Watch the terminal for captured URLs (10 second window)")

    # Log all XHR/fetch via JS monkey-patch
    driver.execute_script("""
    window.__captured = [];
    var origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        window.__captured.push({method: method, url: url});
        return origOpen.apply(this, arguments);
    };
    var origFetch = window.fetch;
    window.fetch = function(url, opts) {
        window.__captured.push({method: 'FETCH', url: String(url)});
        return origFetch.apply(this, arguments);
    };
    """)

    time.sleep(10)  # Window to click a button

    captured = driver.execute_script("return window.__captured;")
    if captured:
        print("\n[*] Captured network requests:")
        for r in captured:
            print(f"  {r['method']} {r['url']}")
        with open(DUMP_FILE.parent / "captured_requests.json", "w") as f:
            json.dump(captured, f, indent=2)
    else:
        print("[!] No requests captured")

    input("\n[Press Enter to close]")

finally:
    driver.quit()
