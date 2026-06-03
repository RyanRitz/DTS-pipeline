"""
brisnet_login_helper.py
========================
Saves your Brisnet session cookies using your real Chrome browser.
Run this ONCE to save your session, then brisnet_download.py handles the rest.

Usage:
    python brisnet_login_helper.py
"""
import json
import time
import subprocess
import sys
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

# ── Load .env once at import time ───────────────────────────────────────────
# Same convention as upload_to_dts.py / cleanup_dts.py.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    # python-dotenv is optional; if absent, env vars must come from the shell.
    pass

COOKIES_FILE = Path(__file__).parent / "brisnet_cookies.json"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\ryanr\AppData\Local\Google\Chrome\Application\chrome.exe",
]

def find_chrome():
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    return None

def save_session():
    chrome = find_chrome()
    if not chrome:
        print("Chrome not found!")
        sys.exit(1)

    print(f"Found Chrome: {chrome}")
    print()

    # Kill any existing Chrome instances so debug port isn't blocked
    print("Closing any existing Chrome windows...")
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                   capture_output=True)
    time.sleep(2)

    # Launch Chrome with remote debugging on a fresh profile
    debug_port = 9222
    user_data = str(Path(__file__).parent / "chrome_debug_profile")
    
    print("Opening Chrome — please log into Brisnet manually...")
    print()
    proc = subprocess.Popen([
        chrome,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "https://www.brisnet.com/product/login",
    ])

    # Wait for Chrome to start
    time.sleep(3)

    print("=" * 50)
    print("Chrome is open at Brisnet login page.")
    print()
    user = os.environ.get("BRISNET_USER", "<set BRISNET_USER in .env>")
    print(f"  1. Type your username: {user}")
    print("  2. Type the password from your .env (BRISNET_PASS)")
    print("  3. Click LOGIN")
    print("  4. Wait until you see your account page")
    print("  5. Come back HERE and press ENTER")
    print("=" * 50)
    print()
    input("Press ENTER when logged in...")

    # Connect and grab cookies
    print("Connecting to Chrome to save cookies...")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(
                f"http://localhost:{debug_port}",
                timeout=10000
            )
            # Get all contexts and pages
            for ctx in browser.contexts:
                cookies = ctx.cookies()
                if any("brisnet" in c.get("domain","") for c in cookies):
                    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
                    print(f"✅ Saved {len(cookies)} Brisnet cookies!")
                    print(f"   File: {COOKIES_FILE}")
                    print()
                    print("You can now run:")
                    print("  python brisnet_download.py --dry-run")
                    browser.close()
                    proc.terminate()
                    return

            # If no brisnet cookies found, save all cookies anyway
            all_cookies = []
            for ctx in browser.contexts:
                all_cookies.extend(ctx.cookies())
            
            COOKIES_FILE.write_text(json.dumps(all_cookies, indent=2))
            print(f"✅ Saved {len(all_cookies)} cookies (may include Brisnet session)")
            browser.close()

        except Exception as e:
            print(f"Error connecting: {e}")
            print()
            print("Trying alternative method...")
            # Alternative: just use requests to grab cookies from the debug API
            try:
                import urllib.request
                import urllib.error
                resp = urllib.request.urlopen(
                    f"http://localhost:{debug_port}/json/version", timeout=5
                )
                print(f"Chrome debug API responding: {resp.read()[:100]}")
            except Exception as e2:
                print(f"Chrome debug API not accessible: {e2}")
                print("Make sure you pressed ENTER AFTER logging in completely.")

    proc.terminate()

if __name__ == "__main__":
    save_session()
