"""
brisnet_download.py
===================
Automated daily downloader for Brisnet PP Data Files (DRF format).

Rules:
  - Only downloads files whose icon shows post positions ARE set
    (the filled icon with horizontal lines — class "drs-icon--pp")
  - Skips entry-only files (empty/outline icon — no PP lines)
  - Skips files already downloaded today
  - Saves to:  <DOWNLOAD_DIR>/<TRACK_CODE>/<DATE>/<TRACKCODE><MMDD>.DRF

Usage:
  python brisnet_download.py [--date YYYYMMDD] [--dry-run] [--headless]

Schedule (Windows Task Scheduler):
  Trigger: Daily at 7:00 AM and again at 12:00 PM
  Action:  python C:\\...\\brisnet_download.py
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Load .env file if present ──────────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Config — edit these ────────────────────────────────────────────────────
BRISNET_USER  = os.getenv("BRISNET_USER",  "YOUR_USERNAME_HERE")
BRISNET_PASS  = os.getenv("BRISNET_PASS",  "YOUR_PASSWORD_HERE")

# Where to save downloaded DRFs
DOWNLOAD_DIR  = Path(os.getenv("BTSM_DRF_DIR",
                r"C:\Users\ryanr\Documents\BTSM\FullAutomation\raw_data"))

# How many calendar days ahead to look (today + N)
DAYS_AHEAD    = 4

# Retry settings
MAX_RETRIES   = 3
RETRY_DELAY   = 5    # seconds between retries

# Brisnet URLs
LOGIN_URL     = "https://www.brisnet.com/cgi-bin/login.cgi"
DRF_PAGE_URL  = "https://www.brisnet.com/product/data-files/DRS"
DOWNLOAD_BASE = "https://www.brisnet.com"
# ───────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("brisnet")


# ── Icon classification ────────────────────────────────────────────────────
#
# Brisnet uses two icon states in the DRF grid:
#
#   READY  (post positions set) — SVG/img has lines drawn through the page
#          CSS class contains "pp" or the <use> href ends with "#icon-pp"
#          or the <img> alt text contains "PP" / the parent <a> has a real href
#
#   NOT READY (entries only)   — icon is hollow/outline, no href or
#          href links to an "entries" page, not a download
#
# We look for <a> tags inside each date-cell whose href contains
# "/product/data-files/DRS/download" (the actual file download link).
# Entry-only cells either have no <a> at all, or link to the entries page.
#
# The icon image distinction:
#   ready    = <img src="...drs_pp*.gif">  or SVG with lines
#   not ready = <img src="...drs_ent*.gif"> or hollow SVG
#
# We detect "ready" by the presence of a real download <a> href.
# ───────────────────────────────────────────────────────────────────────────


def _date_range(days_ahead: int) -> list[str]:
    """Return list of date strings YYYYMMDD for today through today+days_ahead."""
    today  = datetime.now().date()
    return [
        (today + timedelta(days=d)).strftime("%Y%m%d")
        for d in range(days_ahead + 1)
    ]


def _already_downloaded(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 10_000   # >10KB = real file


def _parse_track_date_from_url(url: str) -> tuple[str, str] | None:
    """
    Extract track code and date from a Brisnet download URL.
    Typical URL pattern:
      /cgi-bin/getpps.cgi?type=DRF&trk=CD&file=cd0509.zip
      /product/data-files/DRS/download?track=CD&date=20250509&type=DRF
    """
    # Pattern 1: query-string style
    m = re.search(r'track=([A-Z]{2,3})&date=(\d{8})', url, re.I)
    if m:
        return m.group(1).upper(), m.group(2)

    # Pattern 2: filename style  cdMMDD.zip
    m = re.search(r'file=([a-z]{2,3})(\d{4})\.', url, re.I)
    if m:
        trk  = m.group(1).upper()
        mmdd = m.group(2)
        year = datetime.now().strftime("%Y")
        return trk, f"{year}{mmdd}"

    # Pattern 3: trk= param
    m_trk  = re.search(r'trk=([A-Z]{2,3})',   url, re.I)
    m_date = re.search(r'date=(\d{8})',         url, re.I)
    if m_trk and m_date:
        return m_trk.group(1).upper(), m_date.group(1)

    return None


def run_download(headless: bool = True,
                 dry_run:  bool = False,
                 target_dates: list[str] | None = None) -> dict:
    """
    Main download routine. Returns summary dict.
    Uses Playwright to log in, scrape the DRF grid, and download files.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    if target_dates is None:
        target_dates = _date_range(DAYS_AHEAD)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    summary = {"downloaded": [], "skipped": [], "failed": [], "not_ready": []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx     = browser.new_context(accept_downloads=True)
        page    = ctx.new_page()
        page.set_default_timeout(30_000)

        page.set_default_timeout(30_000)

        # ── Log in using saved cookies ─────────────────────────────────────
        import json as _json
        cookies_file = Path(__file__).parent / "brisnet_cookies.json"
        screenshot_path = str(Path(__file__).parent / "brisnet_debug.png")

        if not cookies_file.exists():
            log.error("No saved session found!")
            log.error("Run this first:  python brisnet_login_helper.py")
            browser.close()
            return summary

        log.info("Loading saved Brisnet session cookies...")
        cookies = _json.loads(cookies_file.read_text())
        ctx.add_cookies(cookies)

        # Test if session is still valid
        page.goto(DRF_PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2000)
        page.screenshot(path=screenshot_path)

        content = page.content().lower()
        if "sign in" in content or "product/login" in page.url:
            log.error("Session expired — run brisnet_login_helper.py again")
            browser.close()
            return summary

        log.info(f"Logged in via saved session ✅ — at: {page.url}")

        # ── Navigate to DRF page ──────────────────────────────────────────
        page.goto(DRF_PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
        # Wait for AngularJS to render track rows — look for first track link
        log.info("Waiting for AngularJS grid to render...")
        try:
            page.wait_for_selector('.tracks .track-row, .tracks a, .table-row a',
                                   timeout=20_000)
            log.info("Grid rendered ✅")
        except Exception:
            log.warning("Grid selector timeout — waiting 10 more seconds...")
            page.wait_for_timeout(10_000)
        page.wait_for_timeout(2000)

        # ── Scrape the download grid ──────────────────────────────────────
        #
        # The page renders a table where:
        #   - Each ROW = one track
        #   - Each COLUMN = one date
        #   - Each CELL may contain an <a> link to a downloadable DRF
        #
        # We look at the column headers to map column index → date,
        # then scan every <a> that links to a real download.
        #
        log.info("Scanning DRF availability grid...")


        # ── Debug: dump page info and all links ──────────────────────────
        log.info(f"Page URL: {page.url}")
        
        # Save page HTML after Angular renders
        html_path = str(Path(__file__).parent / "brisnet_page.html")
        Path(html_path).write_text(page.content(), encoding='utf-8')
        log.info(f"Page HTML saved: {html_path}")
        
        # Count all links
        all_a = page.query_selector_all("a[href]")
        log.info(f"Total links: {len(all_a)}")
        
        # Show ALL hrefs so we can see what's there
        for a in all_a[:30]:
            href = a.get_attribute("href") or ""
            txt  = (a.inner_text() or "")[:20].strip()
            log.info(f"  href={href[:70]}  text={txt}")
        
        # Count track rows  
        track_rows = page.query_selector_all(".track-row, .tracks .ng-scope")
        log.info(f"Track rows found: {len(track_rows)}")

        # Get column date labels from the header row
        # Brisnet renders dates as "May 6", "May 7", etc. in the header
        col_dates = {}
        header_cells = page.query_selector_all("thead th, .drs-header th, table tr:first-child td")

        # Fallback: read from the visible date buttons/headers
        date_headers = page.query_selector_all('[class*="date"], [class*="col-date"], th')
        for i, cell in enumerate(date_headers):
            txt = cell.inner_text().strip()
            # Try to parse "May 6", "5/6", "05/06" etc.
            for fmt in ("%b %d", "%B %d", "%m/%d", "%b\n%d"):
                try:
                    dt = datetime.strptime(txt.replace("\n", " "), fmt)
                    col_dates[i] = dt.replace(year=datetime.now().year).strftime("%Y%m%d")
                    break
                except ValueError:
                    pass

        # ── Find all download links ───────────────────────────────────────
        #
        # Strategy: find every <a href> that looks like a DRF download.
        # Brisnet download links typically contain:
        #   - /cgi-bin/getpps.cgi
        #   - /product/data-files/DRS/download
        #   - a file parameter ending in .zip or .DRF
        #
        # The icon CLASS distinguishes ready vs not-ready.
        # A filled/PP icon <a> will have an href; entry-only icons won't
        # (or the href goes to an entries page, not a file).
        #
        all_links = page.query_selector_all("a[href]")
        download_links = []

        for link in all_links:
            href = link.get_attribute("href") or ""
            href_lower = href.lower()

            # Must look like a DRF file download
            is_download = any(x in href_lower for x in [
                "getpps.cgi", "drs/download", "type=drf",
                ".zip", "pp_drs", "drfpp", "/drf"
            ])
            if not is_download:
                continue

            # Check that the icon indicates PP are set
            # (not just entries — entries icons are hollow, PP icons are filled)
            # We check the parent element's class or the img src
            parent_html = ""
            try:
                parent_html = link.evaluate("el => el.outerHTML").lower()
            except Exception:
                pass

            # Skip if explicitly marked as entries-only
            if any(x in parent_html for x in ["ent-icon", "entries-only", "drs_ent"]):
                continue

            # Build absolute URL
            if href.startswith("/"):
                href = DOWNLOAD_BASE + href
            elif not href.startswith("http"):
                href = DOWNLOAD_BASE + "/" + href

            parsed = _parse_track_date_from_url(href)
            if parsed:
                trk, date_str = parsed
                if date_str in target_dates:
                    download_links.append((trk, date_str, href))

        # ── Alternative scrape: read the full page HTML ───────────────────
        # If link scraping found nothing (page uses JS rendering differently),
        # fall back to parsing the raw HTML for all anchor hrefs
        if not download_links:
            log.warning("Link scan found nothing — trying HTML parse fallback")
            html = page.content()
            hrefs = re.findall(r'href="([^"]*(?:getpps|drs.*download|type=drf)[^"]*)"',
                               html, re.I)
            for href in hrefs:
                if href.startswith("/"):
                    href = DOWNLOAD_BASE + href
                parsed = _parse_track_date_from_url(href)
                if parsed:
                    trk, date_str = parsed
                    if date_str in target_dates:
                        download_links.append((trk, date_str, href))

        log.info(f"Found {len(download_links)} downloadable DRF files")

        # ── Download each file ────────────────────────────────────────────
        for trk, date_str, url in sorted(set(download_links)):
            mmdd    = date_str[4:]   # MMDD
            fname   = f"{trk.upper()}{mmdd}.DRF"
            out_dir = DOWNLOAD_DIR / trk.upper()
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / fname

            if _already_downloaded(out_path):
                log.info(f"  SKIP  {fname} (already downloaded)")
                summary["skipped"].append(str(out_path))
                continue

            if dry_run:
                log.info(f"  DRY   {fname}  ← {url}")
                summary["downloaded"].append(str(out_path))
                continue

            log.info(f"  GET   {fname}  ← {url}")
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    with page.expect_download(timeout=60_000) as dl_info:
                        page.goto(url)
                    download = dl_info.value
                    download.save_as(str(out_path))
                    sz = out_path.stat().st_size
                    log.info(f"         Saved {sz:,} bytes → {out_path}")
                    summary["downloaded"].append(str(out_path))
                    break
                except Exception as e:
                    log.warning(f"         Attempt {attempt}/{MAX_RETRIES} failed: {e}")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    else:
                        log.error(f"         FAILED: {fname}")
                        summary["failed"].append(str(out_path))

        browser.close()

    return summary


# ── Alternate approach: requests + BeautifulSoup (faster, no browser) ──────
def _icon_has_pp(anchor) -> bool:
    """
    Return True if this Brisnet download icon indicates post positions are set.

    Brisnet's DRF page uses two icon states:
      READY (PP set)    — filled icon, lines visible through the document image
                          img src contains 'pp' or SVG use href ends '#icon-pp'
                          OR the anchor href directly links to a zip/DRF file
                          OR data-type="DRF" attribute is present

      NOT READY         — hollow/outline icon (entries only)
                          img src contains 'ent' (entries), SVG '#icon-ent'
                          OR href goes to /entries page
                          OR data-type="ENT" attribute

    The most reliable signal: if the href URL ends in .zip or contains
    'type=DRF' it's a real PP download regardless of the icon class.
    """
    href = anchor.get("href", "").lower()

    # Hard positive: direct file download link
    if href.endswith(".zip") or "type=drf" in href:
        return True

    # Hard negative: entries page link
    if "type=ent" in href or href.endswith(".ent"):
        return False

    # Check data attributes Brisnet sometimes uses
    dtype = (anchor.get("data-type") or anchor.get("data-filetype") or "").lower()
    if dtype == "drf": return True
    if dtype == "ent": return False

    # Check icon image src
    img = anchor.find("img")
    if img:
        src = (img.get("src") or "").lower()
        # pp icon = lines through the page graphic
        if "_pp" in src or "pp_" in src or src.endswith("pp.gif"):
            return True
        # ent icon = hollow
        if "_ent" in src or "ent_" in src or src.endswith("ent.gif"):
            return False

    # Check SVG use element (inline SVG icons)
    use = anchor.find("use")
    if use:
        xlink = (use.get("href") or use.get("xlink:href") or "").lower()
        if "pp" in xlink:  return True
        if "ent" in xlink: return False

    # Check anchor class
    cls = " ".join(anchor.get("class") or []).lower()
    if "pp" in cls:  return True
    if "ent" in cls: return False

    # Check title/aria attributes
    title = (anchor.get("title") or anchor.get("aria-label") or "").lower()
    if "past performance" in title or "pp" in title: return True
    if "entries" in title or " ent" in title:        return False

    # Default: if it has a valid-looking download href, assume PP
    if "getpps" in href or "download" in href:
        return True

    return False  # unknown — skip


def run_download_requests(dry_run: bool = False,
                          target_dates: list[str] | None = None) -> dict:
    """
    Lighter-weight downloader using requests session.
    More reliable than browser automation for simple file downloads.
    Falls back to Playwright if login requires JS.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("requests/bs4 not available — using Playwright")
        return run_download(headless=True, dry_run=dry_run,
                            target_dates=target_dates)

    if target_dates is None:
        target_dates = _date_range(DAYS_AHEAD)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    summary = {"downloaded": [], "skipped": [], "failed": [], "not_ready": []}

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    })

    # ── Log in ────────────────────────────────────────────────────────────
    log.info("Logging in to Brisnet (requests session)...")

    # Must visit the login page first to get session cookies and referrer
    try:
        resp = session.get(
            "https://www.brisnet.com/account/login/",
            timeout=60,
            allow_redirects=True,
        )
        log.info(f"Login page: {resp.status_code}")
    except Exception:
        # Try alternate login page URLs
        try:
            resp = session.get(
                "https://www.brisnet.com/cgi-bin/login.cgi",
                timeout=60,
            )
        except Exception as e:
            log.error(f"Cannot reach Brisnet login page: {e}")
            return summary

    # Parse any hidden form fields (CSRF tokens etc.)
    soup0 = BeautifulSoup(resp.text, "html.parser")
    login_data = {
        "username": BRISNET_USER,
        "password": BRISNET_PASS,
        "submit":   "Login",
        "remember": "1",
    }
    form = soup0.find("form")
    if form:
        for inp in form.find_all("input", type="hidden"):
            if inp.get("name"):
                login_data[inp["name"]] = inp.get("value", "")
        action = form.get("action", "")
        if action and not action.startswith("http"):
            action = "https://www.brisnet.com" + action
        login_url = action or "https://www.brisnet.com/cgi-bin/login.cgi"
    else:
        login_url = "https://www.brisnet.com/cgi-bin/login.cgi"

    # Submit login with proper referrer header
    session.headers.update({"Referer": resp.url})
    try:
        resp = session.post(login_url, data=login_data, timeout=60,
                            allow_redirects=True)
        log.info(f"Login POST: {resp.status_code} → {resp.url}")
    except Exception as e:
        log.error(f"Login POST failed: {e}")
        return summary

    # Check if logged in
    logged_in = any(x in resp.text.lower() for x in
                    ["logout", "my account", "my products", "sign out",
                     BRISNET_USER.lower(), "28ritzr"])
    if logged_in:
        log.info("Login confirmed ✅")
    else:
        log.warning("Login confirmation unclear — trying DRF page anyway")

    # ── Load DRF page ─────────────────────────────────────────────────────
    log.info("Loading DRF availability page...")
    session.headers.update({"Referer": "https://www.brisnet.com/"})
    try:
        resp = session.get(DRF_PAGE_URL, timeout=60)
        log.info(f"DRF page: {resp.status_code} ({len(resp.text):,} chars) → {resp.url}")
    except Exception as e:
        log.error(f"Could not load DRF page: {e}")
        return summary
    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Parse the availability table ─────────────────────────────────────
    #
    # Find every anchor whose href looks like a PP download
    # and whose icon indicates post positions are set.
    #
    # Icon logic:
    #   The icon images have src filenames like:
    #     drs_pp.gif  or  icon-pp.svg   → POST POSITIONS SET ✅
    #     drs_ent.gif or  icon-ent.svg  → entries only ❌
    #
    #   Also: if the <a> href contains 'type=DRF' or 'getpps' with a zip/drf
    #   extension, it's a real download link (PP set).
    #   If href goes to /entries or contains 'type=ENT', it's entries only.
    #
    pp_links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        href_l = href.lower()

        # Is this a download link?
        is_dl = any(x in href_l for x in [
            "getpps.cgi", "type=drf", ".zip", "/drs/download",
            "pp_drs", "drfpp"
        ])
        if not is_dl:
            continue

        # Check icon state — must be PP-ready
        if not _icon_has_pp(a):
            log.debug(f"  SKIP (not PP-ready): {href[:60]}")
            continue

        # Build absolute URL
        if href.startswith("/"):
            href = "https://www.brisnet.com" + href

        parsed = _parse_track_date_from_url(href)
        if parsed:
            trk, date_str = parsed
            if date_str in target_dates:
                pp_links.append((trk, date_str, href))

    # Also scan raw HTML for download links using regex
    # (catches dynamically constructed hrefs that BS4 might miss)
    raw_hrefs = re.findall(
        r'href="(/[^"]*(?:getpps\.cgi|drs/download)[^"]*)"',
        resp.text, re.I
    )
    for raw in raw_hrefs:
        full = "https://www.brisnet.com" + raw
        parsed = _parse_track_date_from_url(full)
        if parsed:
            trk, date_str = parsed
            if date_str in target_dates:
                pp_links.append((trk, date_str, full))

    # Deduplicate
    pp_links = list({(t, d, u) for t, d, u in pp_links})
    log.info(f"Found {len(pp_links)} PP-ready DRF files to download")

    if not pp_links:
        log.warning("No download links found — page may require JS. "
                    "Try running with --browser flag.")

    # ── Download each file ────────────────────────────────────────────────
    for trk, date_str, url in sorted(pp_links):
        mmdd     = date_str[4:]
        fname    = f"{trk.upper()}{mmdd}.DRF"
        out_dir  = DOWNLOAD_DIR / trk.upper()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / fname

        if _already_downloaded(out_path):
            log.info(f"  SKIP  {fname}")
            summary["skipped"].append(str(out_path))
            continue

        if dry_run:
            log.info(f"  DRY   {fname}  ← {url}")
            summary["downloaded"].append(str(out_path))
            continue

        log.info(f"  GET   {fname}")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = session.get(url, timeout=60, stream=True)
                resp.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in resp.iter_content(65536):
                        f.write(chunk)
                sz = out_path.stat().st_size
                log.info(f"         Saved {sz:,} bytes → {out_path}")
                summary["downloaded"].append(str(out_path))
                break
            except Exception as e:
                log.warning(f"         Attempt {attempt}/{MAX_RETRIES}: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    log.error(f"         FAILED: {fname}")
                    summary["failed"].append(str(out_path))

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Brisnet DRF auto-downloader")
    p.add_argument("--date",     help="Single date YYYYMMDD (default: today + 4 days)")
    p.add_argument("--days",     type=int, default=DAYS_AHEAD,
                   help=f"How many days ahead to check (default {DAYS_AHEAD})")
    p.add_argument("--dry-run",  action="store_true",
                   help="Print what would download, don't actually download")
    p.add_argument("--browser",    action="store_true", default=True,
                   help="Use Playwright browser (default: True)")
    p.add_argument("--no-browser", action="store_true", dest="no_browser",
                   help="Use requests instead of browser (may fail on Brisnet)")
    p.add_argument("--headless", action="store_true", default=True,
                   help="Run browser headlessly (default True)")
    p.add_argument("--show",     action="store_true",
                   help="Show browser window (disables headless)")
    p.add_argument("--user",     help="Brisnet username (overrides env var)")
    p.add_argument("--pass",     dest="password",
                   help="Brisnet password (overrides env var)")
    p.add_argument("--out",      help="Download directory (overrides default)")
    global BRISNET_USER, BRISNET_PASS, DOWNLOAD_DIR
    args = p.parse_args()

    # Apply overrides
    if args.user:     BRISNET_USER = args.user
    if args.password: BRISNET_PASS = args.password
    if args.out:      DOWNLOAD_DIR = Path(args.out)
    days_ahead = args.days

    # Validate credentials
    if BRISNET_USER in ("", "YOUR_USERNAME_HERE"):
        log.error("No Brisnet username set. Use --user or set BRISNET_USER env var.")
        sys.exit(1)
    if BRISNET_PASS in ("", "YOUR_PASSWORD_HERE"):
        log.error("No Brisnet password set. Use --pass or set BRISNET_PASS env var.")
        sys.exit(1)

    # Build date list
    if args.date:
        dates = [args.date]
    else:
        dates = _date_range(days_ahead)

    log.info(f"Target dates: {dates}")
    log.info(f"Download dir: {DOWNLOAD_DIR}")

    # Run — default to browser mode since Brisnet requires real browser session
    headless = False if args.show else True
    if not args.no_browser:
        result = run_download(headless=headless, dry_run=args.dry_run,
                              target_dates=dates)
    else:
        result = run_download_requests(dry_run=args.dry_run,
                                       target_dates=dates)

    # Summary
    log.info("─" * 60)
    log.info(f"Downloaded : {len(result['downloaded'])}")
    log.info(f"Skipped    : {len(result['skipped'])} (already had)")
    log.info(f"Failed     : {len(result['failed'])}")
    if result['downloaded']:
        log.info("New files:")
        for f in result['downloaded']:
            log.info(f"  {f}")
    if result['failed']:
        log.warning("Failed files:")
        for f in result['failed']:
            log.warning(f"  {f}")

    sys.exit(1 if result['failed'] else 0)


if __name__ == "__main__":
    main()
