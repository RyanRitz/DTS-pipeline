#!/usr/bin/env python3
"""
brisnet_ccf.py — Comprehensive Chart Files (results/charts) downloader.

The charts are the OUTCOME half of the modelling data: DRS gives predictors,
CCF gives who actually won. Without them the accumulated DRS cards can't fit a
model. This is the twin of brisnet_history.py, pointed at the chart product.

Reuses brisnet_download.py's proven auth/driver/download stack. That module
reads PRODUCT_CODE at call time, so we just re-point it - the production DRS
downloader is untouched.

    # 1) DISCOVER - what chart product code exists, and HOW FAR BACK it goes.
    #    Reads every productCode straight off the grid; nothing is guessed.
    python brisnet_ccf.py --discover

    # 2) PROBE - try specific dates once you know the code
    python brisnet_ccf.py --product CCF --probe --tracks CD --test-dates 2026-07-10,2026-06-15

    # 3) PULL - bulk fetch a date range into a raw landing dir
    python brisnet_ccf.py --product CCF --tracks CD,DMR --start 2026-07-01 --end 2026-07-31

Files land RAW (unrenamed) in --dest. Format is inspected before we write the
archiver, because we don't yet know if CCF ships one file per card or per race.
"""
from __future__ import annotations
import argparse, json, os, re, time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import brisnet_download as bd
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

log = bd.log

DISCOVER_JS = r"""
var rows = document.querySelectorAll('.track.table-row');
var out = {};
for (var i = 0; i < rows.length; i++) {
    var s = angular.element(rows[i]).scope();
    if (!s || !s.track) continue;
    var t = s.track;
    var dates = t.availableDates || [];
    for (var j = 0; j < dates.length; j++) {
        var prods = dates[j].availableProducts || [];
        for (var k = 0; k < prods.length; k++) {
            var p = prods[k];
            var c = p.productCode;
            if (!out[c]) out[c] = {code:c, n:0, minD:null, maxD:null, avail:{}, races:{}, tracks:{}};
            var o = out[c];
            o.n++;
            var d = dates[j].productDate;
            if (!o.minD || d < o.minD) o.minD = d;
            if (!o.maxD || d > o.maxD) o.maxD = d;
            o.avail[p.customerAvailability] = (o.avail[p.customerAvailability]||0)+1;
            o.races[p.raceNumber||0] = true;
            o.tracks[t.trackCode] = true;
        }
    }
}
return JSON.stringify(out);
"""


TRACKS_JS = r"""
var rows = document.querySelectorAll('.track.table-row');
var out = [];
var seen = {};
for (var i = 0; i < rows.length; i++) {
    var s = angular.element(rows[i]).scope();
    if (!s || !s.track) continue;
    var t = s.track;
    if (seen[t.trackCode]) continue;
    seen[t.trackCode] = true;
    var offers = 0;
    var dates = t.availableDates || [];
    for (var j = 0; j < dates.length; j++) {
        var prods = dates[j].availableProducts || [];
        for (var k = 0; k < prods.length; k++) {
            if (prods[k].productCode === arguments[0]) offers++;
        }
    }
    out.push({trackCode:t.trackCode, trackName:t.trackName, country:t.country,
              trackType:t.trackType, dayEvening:t.dayEvening, offers:offers});
}
return JSON.stringify(out);
"""


def all_track_attrs(driver, product):
    """
    Track attrs for URL building, WITHOUT bd.extract_tracks()'s
    customerAvailability=='View' filter. That filter is too strict: the grid
    reports 'AddToCart' for every offer yet the files download fine (verified on
    SAR 2026-07-11). Filtering on View yields an empty track list and silently
    downloads nothing.
    """
    out = {}
    for t in json.loads(driver.execute_script(TRACKS_JS, product)):
        out[(t.get("trackCode") or "").upper()] = t
    return out


DRF_NAME = re.compile(r"^(\d{4})(\d{2})(\d{2})_([A-Za-z]{2,4})_DRS$", re.I)
CANON_FOLDER = {"CD": "CDX", "GP": "GPX", "FG": "FGX"}


def manifest_from_drfs(roots, start=None, end=None):
    """
    Build the exact (date, track) list of cards that ACTUALLY RACED, by reading
    the DRF filenames we already hold (YYYYMMDD_TRACK_DRS.DRF).

    Why: a blind date-range x track sweep is ~5,000 attempts for Mar-Jul, and
    the overwhelming majority are dark days that each burn a full download
    timeout. Pairing off the DRFs cuts it to ~650 real fetches and guarantees
    every chart we pull matches a card we have predictors for - which is the
    entire point (a chart with no DRF, or a DRF with no chart, can't fit).
    """
    seen = set()
    for root in roots:
        root = Path(root)
        if not root.exists():
            print(f"  [!] pair-from path not found: {root}")
            continue
        for f in root.rglob("*.DRF"):
            m = DRF_NAME.match(f.stem)
            if not m:
                continue                      # legacy TRACKMMDD.DRF has no year - skip
            y, mo, d, trk = m.groups()
            try:
                dt = datetime(int(y), int(mo), int(d)).date()
            except ValueError:
                continue
            if start and dt < start: continue
            if end and dt > end:     continue
            seen.add((dt, trk.upper()))
    return sorted(seen)


def charts_already_local(dest_btsm: Path, dt, track: str) -> bool:
    """True if this card's charts are already filed (any of the 6 parts)."""
    folder = CANON_FOLDER.get(track.upper(), track.upper())
    ydir = Path(dest_btsm) / folder / "RAW_DATA" / "RESULTS" / f"{dt:%Y}"
    if not ydir.is_dir():
        return False
    stem = f"{track.upper()}{dt:%m%d%Y}"
    try:
        with os.scandir(ydir) as it:
            return any(e.name.upper().startswith(stem) for e in it)
    except OSError:
        return False


_DIAG_SAVED = [False]


def _diagnose_reject(p: Path) -> None:
    """
    The FIRST rejected page gets saved + sniffed. A reject can mean two very
    different things and they are indistinguishable by status alone:
      - "not owned"  -> a cart/purchase page   (expected, harmless)
      - "not logged in" -> a login page        (BUG - the whole run is useless)
    Chrome cannot copy Cookies while Chrome is running (WinError 32 on
    Network\Cookies), so an un-authenticated session is a very real failure mode.
    """
    if _DIAG_SAVED[0]:
        return
    _DIAG_SAVED[0] = True
    try:
        raw = p.open("rb").read(4000).decode("utf-8", "ignore").lower()
    except OSError:
        return
    out = Path("diag_reject.html")
    try:
        out.write_bytes(p.open("rb").read())
    except OSError:
        pass
    hints = []
    for kw, msg in [("password", "LOGIN PAGE - session is NOT authenticated"),
                    ("sign in", "LOGIN PAGE - session is NOT authenticated"),
                    ("log in", "LOGIN PAGE - session is NOT authenticated"),
                    ("add to cart", "cart/purchase page - card not owned"),
                    ("shopping cart", "cart/purchase page - card not owned"),
                    ("not authorized", "not authorized"),
                    ("no data", "no data for this track/date")]:
        if kw in raw:
            hints.append(msg)
    log.warning(f"[!] First rejected download saved to {out} ({p.stat().st_size} bytes)")
    if hints:
        for h in dict.fromkeys(hints):
            log.warning(f"    -> looks like: {h}")
    else:
        log.warning(f"    -> unrecognised page; open {out} to see what Brisnet returned")


def _is_real_data(p: Path) -> bool:
    """
    Reject Brisnet's "you don't own this" HTML page, which Chrome saves as
    downloads.htm. A naive head[:1]=='<' test misses it because the page can
    start with a BOM/whitespace, so we strip those first and also reject the
    obvious HTML/JSON extensions outright.
    """
    if p.suffix.lower() in (".htm", ".html", ".json", ".txt"):
        return False
    try:
        if p.stat().st_size < 200:
            return False
        head = p.open("rb").read(512)
    except OSError:
        return False
    t = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return t[:1] not in (b"<", b"{")


def _safe_unlink(p: Path, tries: int = 6) -> None:
    for _ in range(tries):
        try:
            p.unlink(); return
        except PermissionError:
            time.sleep(0.4)          # Chrome may still hold the handle
        except OSError:
            return


def _safe_move(src: Path, dst: Path, tries: int = 10) -> bool:
    """Chrome can still have the file open the instant it appears -> WinError 32."""
    for _ in range(tries):
        try:
            src.replace(dst); return True
        except PermissionError:
            time.sleep(0.5)
        except OSError:
            return False
    return False


def drf_already_local(dest_btsm: Path, dt, track: str) -> bool:
    """True if this card's DRF is already filed (archive_drfs naming)."""
    folder = CANON_FOLDER.get(track.upper(), track.upper())
    f = Path(dest_btsm) / folder / "RAW_DATA" / "RACINGFORM" / f"{dt:%Y}" / f"{track.upper()}{dt:%m%d}.DRF"
    return f.exists()


def already_have(product: str, dest_btsm: Path, dt, track: str) -> bool:
    return (charts_already_local(dest_btsm, dt, track) if product.upper() == "CCF"
            else drf_already_local(dest_btsm, dt, track))


def authenticate(page_url):
    driver = bd.get_driver()
    driver.get("https://www.brisnet.com"); time.sleep(2); bd.dismiss_cookie_banner(driver)
    driver.get(page_url); time.sleep(3); bd.dismiss_cookie_banner(driver)
    if not bd.is_logged_in(driver):
        log.info("[*] Not logged in via profile — typing credentials")
        if not bd.try_typed_login(driver, os.environ.get("BRISNET_USER",""), os.environ.get("BRISNET_PASS","")):
            raise SystemExit("[!] Brisnet login failed — check BRISNET_USER/BRISNET_PASS in .env")
        driver.get(page_url); time.sleep(3); bd.dismiss_cookie_banner(driver)
    WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row")))
    bd.wait_for_angular_data(driver, timeout=30)
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        body = ""
    signed_in = any(k in body for k in ("logout", "sign out", "my account"))
    if not signed_in:
        log.warning("[!] Session does NOT look authenticated (no logout/my-account link).")
        log.warning("    The data-files grid renders for anonymous users too, so a populated")
        log.warning("    grid does NOT mean you are logged in - downloads will return a login page.")
        log.warning("    CLOSE ALL CHROME WINDOWS and re-run: Chrome locks Network\\Cookies while")
        log.warning("    running, so the profile copy starts with no session.")
    else:
        log.info("[*] Session looks authenticated.")
    return driver


def do_discover(driver):
    data = json.loads(driver.execute_script(DISCOVER_JS))
    if not data:
        log.warning("[!] No products found in the grid scope.")
        return
    log.info("=" * 78)
    log.info("  PRODUCTS VISIBLE ON THE GRID  (code | offers | date range | availability)")
    log.info("=" * 78)
    for c, o in sorted(data.items()):
        races = sorted(o["races"].keys())
        per = "whole-card (race 0)" if races == ["0"] or races == [0] else f"per-race (raceNumber={races[:6]})"
        log.info(f"  {c:6} n={o['n']:<5} {o['minD']} .. {o['maxD']}")
        log.info(f"         availability={o['avail']}  tracks={len(o['tracks'])}  {per}")
    log.info("")
    log.info("  -> 'View' = you can download it. The date range above is what the GRID")
    log.info("     exposes; older dates may still work via direct URL (try --probe).")


def pull_one(driver, code, attrs, d, race, dest, timeout=25):
    track = {"trackCode": code, "country": attrs["country"],
             "trackType": attrs["trackType"], "dayEvening": attrs["dayEvening"]}
    url = bd.build_url(track, d.isoformat(), race)
    got = bd.download_via_browser(driver, url, timeout=timeout)
    if not got:
        return None
    got = Path(got)
    if not _is_real_data(got):
        _diagnose_reject(got)        # tells us: not-owned vs NOT-LOGGED-IN
        _safe_unlink(got)
        return None
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{d:%Y%m%d}_{code}_{bd.PRODUCT_CODE}_r{race}{got.suffix}"
    if target.exists():
        _safe_unlink(target)
    if not _safe_move(got, target):
        log.warning(f"    could not move {got.name} (locked) - leaving in place")
        return None
    return target


def main():
    ap = argparse.ArgumentParser(description="Brisnet Comprehensive Chart Files downloader")
    ap.add_argument("--discover", action="store_true", help="list every product code + date range, then exit")
    ap.add_argument("--product", default=None, help="product code to pull (from --discover), e.g. CCF")
    ap.add_argument("--page", default=None, help="data-files page to load (default: the product's own page)")
    ap.add_argument("--tracks", default="", help="comma list of BRIS track codes")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--probe", action="store_true"); ap.add_argument("--test-dates", default="")
    ap.add_argument("--race", type=int, default=0, help="0=whole card; else race number")
    ap.add_argument("--dest", default=str(Path(bd.OUTPUT_DIR).parent / "CCF_Downloads"))
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--pair-from", default="", help="comma list of dirs holding YYYYMMDD_TRACK_DRS.DRF - fetch charts ONLY for cards that raced")
    ap.add_argument("--btsm", default=str(Path(bd.OUTPUT_DIR).parent.parent), help="local BTSM root (to skip cards already filed)")
    ap.add_argument("--timeout", type=int, default=25, help="per-download wait. Dark days burn the FULL timeout, so use ~8 for wide historical sweeps")
    ap.add_argument("--no-skip", action="store_true", help="do not skip cards already on disk")
    a = ap.parse_args()

    page = a.page or (f"https://www.brisnet.com/product/data-files/{a.product}" if a.product else bd.DRS_URL)
    if a.product:
        bd.PRODUCT_CODE = a.product          # build_url + extract_tracks read this at call time
        bd.DRS_URL = page

    driver = authenticate(page)
    try:
        if a.discover or not a.product:
            do_discover(driver)
            return
        live = all_track_attrs(driver, a.product)
        offering = [c for c, t in live.items() if t.get("offers")]
        log.info(f"[*] {len(live)} track(s) on the grid; {len(offering)} offer {a.product}: {', '.join(sorted(offering))}")
        codes = [c.strip().upper() for c in a.tracks.split(",") if c.strip()] or sorted(live)

        if a.probe:
            for c in codes:
                at = live.get(c)
                if not at:
                    log.warning(f"[probe] {c}: not on the grid today — cannot build URL; skipping"); continue
                for ds in [x for x in a.test_dates.split(",") if x.strip()]:
                    d = datetime.fromisoformat(ds).date()
                    t = pull_one(driver, c, at, d, a.race, a.dest)
                    log.info(f"[probe] {c} {d}: " + (f"OK -> {t.name} ({t.stat().st_size} bytes)" if t
                             else "no file (dark day / not entitled / too far back)"))
                    time.sleep(a.sleep)
            return

        if a.pair_from:
            start = datetime.fromisoformat(a.start).date() if a.start else None
            end   = datetime.fromisoformat(a.end).date()   if a.end   else None
            cards = manifest_from_drfs([x.strip() for x in a.pair_from.split(",") if x.strip()], start, end)
            if codes:
                cards = [(d, t) for (d, t) in cards if t in codes]
            todo = [(d, t) for (d, t) in cards if not charts_already_local(Path(a.btsm), d, t)]
            print(f"[*] {len(cards)} card(s) raced in range; {len(cards)-len(todo)} already have charts; fetching {len(todo)}")
            got = defaultdict(int); miss = 0
            for i, (d, t) in enumerate(todo, 1):
                at = live.get(t)
                if not at:
                    miss += 1; continue
                f = pull_one(driver, t, at, d, a.race, a.dest)
                if f: got[t] += 1
                else: miss += 1
                if i % 25 == 0:
                    print(f"    ...{i}/{len(todo)}  ok={sum(got.values())} miss={miss}", flush=True)
                time.sleep(a.sleep)
            print("=" * 50)
            for c, n in sorted(got.items()): print(f"  {c}: {n} chart zip(s)")
            print(f"  fetched {sum(got.values())}, missed {miss}. Raw zips in: {a.dest}")
            print("  -> now run:  python archive_charts.py")
            return

        if not (a.start and a.end): raise SystemExit("[!] need --start and --end (or --probe/--discover/--pair-from)")
        s = datetime.fromisoformat(a.start).date(); e = datetime.fromisoformat(a.end).date()
        got = defaultdict(int)
        for c in codes:
            at = live.get(c)
            if not at:
                log.warning(f"[!] {c}: not offering {a.product} today — skipping"); continue
            d = s; tried = have = 0
            while d <= e:
                if not a.no_skip and already_have(a.product, Path(a.btsm), d, c):
                    have += 1; d += timedelta(days=1); continue
                tried += 1
                t = pull_one(driver, c, at, d, a.race, a.dest, a.timeout)
                if t: got[c] += 1; log.info(f"    {c} {d}: OK -> {t.name}")
                if tried % 25 == 0:
                    log.info(f"    ...{c} through {d}: tried={tried} ok={got[c]} (skipped {have} already local)")
                time.sleep(a.sleep); d += timedelta(days=1)
            log.info(f"[*] {c}: {got[c]} downloaded, {have} already local, {tried-got[c]} not available")
        log.info("=" * 50)
        for c, n in sorted(got.items()): log.info(f"  {c}: {n} file(s)")
        log.info(f"  raw files in: {a.dest}")
    finally:
        try: driver.quit()
        except Exception: pass


if __name__ == "__main__":
    main()
