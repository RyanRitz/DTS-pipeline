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
import argparse, json, os, time
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


def pull_one(driver, code, attrs, d, race, dest):
    track = {"trackCode": code, "country": attrs["country"],
             "trackType": attrs["trackType"], "dayEvening": attrs["dayEvening"]}
    url = bd.build_url(track, d.isoformat(), race)
    got = bd.download_via_browser(driver, url, timeout=25)
    if not got:
        return None
    got = Path(got)
    try:
        head = got.open("rb").read(4)
    except Exception:
        return None
    if head[:1] in (b"<", b"{") or got.stat().st_size < 200:
        try: got.unlink()
        except Exception: pass
        return None
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{d:%Y%m%d}_{code}_{bd.PRODUCT_CODE}_r{race}{got.suffix}"
    if target.exists(): target.unlink()
    got.rename(target)
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
        live = {}
        for t in bd.extract_tracks(driver):
            live[(t.get("trackCode") or "").upper()] = {
                "country": t["country"], "trackType": t["trackType"],
                "dayEvening": t["dayEvening"], "trackName": t.get("trackName","")}
        log.info(f"[*] {len(live)} track(s) offer {a.product} right now: {', '.join(sorted(live))}")
        codes = [c.strip().upper() for c in a.tracks.split(",") if c.strip()] or sorted(live)

        if a.probe:
            for c in codes:
                at = live.get(c)
                if not at:
                    log.warning(f"[probe] {c}: not offering {a.product} today — skipping"); continue
                for ds in [x for x in a.test_dates.split(",") if x.strip()]:
                    d = datetime.fromisoformat(ds).date()
                    t = pull_one(driver, c, at, d, a.race, a.dest)
                    log.info(f"[probe] {c} {d}: " + (f"OK -> {t.name} ({t.stat().st_size} bytes)" if t
                             else "no file (dark day / not entitled / too far back)"))
                    time.sleep(a.sleep)
            return

        if not (a.start and a.end): raise SystemExit("[!] need --start and --end (or --probe/--discover)")
        s = datetime.fromisoformat(a.start).date(); e = datetime.fromisoformat(a.end).date()
        got = defaultdict(int)
        for c in codes:
            at = live.get(c)
            if not at:
                log.warning(f"[!] {c}: not offering {a.product} today — skipping"); continue
            d = s
            while d <= e:
                t = pull_one(driver, c, at, d, a.race, a.dest)
                if t: got[c] += 1; log.info(f"    {c} {d}: OK -> {t.name}")
                time.sleep(a.sleep); d += timedelta(days=1)
        log.info("=" * 50)
        for c, n in sorted(got.items()): log.info(f"  {c}: {n} file(s)")
        log.info(f"  raw files in: {a.dest}")
    finally:
        try: driver.quit()
        except Exception: pass


if __name__ == "__main__":
    main()
