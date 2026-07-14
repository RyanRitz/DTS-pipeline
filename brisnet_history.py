#!/usr/bin/env python3
"""
brisnet_history.py — historical DRS (past-performance DRF) downloader.

Reuses the proven auth / driver / download machinery in brisnet_download.py and
walks a date range for a set of tracks, saving each card's single-file DRF into
a per-track archive:

    <dest>/<FOLDER>/RAW_DATA/RACINGFORM/<YEAR>/<TRACK><MMDD>.DRF

Track URL attributes (country / trackType / dayEvening) are discovered LIVE from
the BRISnet grid via extract_tracks(), so we never hard-guess the trackType
token. Tracks not carded on the day you run this fall back to FALLBACK_ATTRS
(fill those in from a --probe run — the token is the same for US TB tracks).

Run the PROBE first to confirm historical pulls work and how far back your plan
reaches, THEN do a bulk pull:

    # probe: dump the visible grid + try a few past dates for a carded track
    python brisnet_history.py --probe --tracks GP --test-dates 2024-01-06,2022-02-05

    # bulk: pull the dirt-era archive once you know the reach
    python brisnet_history.py --tracks DMR,GP,SA,CD --start 2016-01-01 --end 2025-12-31
"""
from __future__ import annotations
import argparse, os, shutil, time
from datetime import datetime, timedelta
from pathlib import Path

import brisnet_download as bd          # reuse auth/driver/download helpers
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

log = bd.log

# Fill from a --probe run for tracks that aren't carded the day you run a bulk
# pull (trackType is a stable token, shared across US thoroughbred tracks):
#   "DMR": {"country": "USA", "trackType": "<token>", "dayEvening": "D"},
FALLBACK_ATTRS: dict[str, dict] = {}

# BRIS trackCode -> DTS archive folder name
FOLDER_FOR = {"DMR": "DMR", "GP": "GPX", "GPX": "GPX", "SA": "SAX", "SAX": "SAX",
              "CD": "CDX", "CDX": "CDX", "KEE": "KEE", "SAR": "SAR"}


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def authenticate():
    """Replicate brisnet_download.main()'s login flow; return a driver on grid."""
    driver = bd.get_driver()
    driver.get("https://www.brisnet.com"); time.sleep(2); bd.dismiss_cookie_banner(driver)
    driver.get(bd.DRS_URL); time.sleep(3); bd.dismiss_cookie_banner(driver)
    user = os.environ.get("BRISNET_USER", ""); pw = os.environ.get("BRISNET_PASS", "")
    if not bd.is_logged_in(driver):
        log.info("[*] Not logged in via profile — typing credentials")
        if not bd.try_typed_login(driver, user, pw):
            raise SystemExit("[!] Brisnet login failed — check BRISNET_USER/BRISNET_PASS in .env")
        driver.get(bd.DRS_URL); time.sleep(3); bd.dismiss_cookie_banner(driver)
    WebDriverWait(driver, 25).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.track.table-row")))
    bd.wait_for_angular_data(driver, timeout=30)
    return driver


def visible_attrs(driver) -> dict[str, dict]:
    out = {}
    for t in bd.extract_tracks(driver):
        out[(t.get("trackCode") or "").upper()] = {
            "country": t["country"], "trackType": t["trackType"],
            "dayEvening": t["dayEvening"], "trackName": t.get("trackName", "")}
    return out


def attrs_for(code, live):
    return live.get(code.upper()) or FALLBACK_ATTRS.get(code.upper())


def _looks_like_drf(p: Path) -> bool:
    """A real single-file DRF is a zip (PK...) or CSV; an error page is HTML/JSON."""
    try:
        head = p.open("rb").read(4)
    except Exception:
        return False
    if head[:1] in (b"<", b"{"):      # HTML error page / JSON error blob
        return False
    return p.stat().st_size > 500


def pull_one(driver, code, attrs, d, dest_base):
    track = {"trackCode": code, "country": attrs["country"],
             "trackType": attrs["trackType"], "dayEvening": attrs["dayEvening"]}
    url = bd.build_url(track, d.isoformat(), 0)     # race 0 = whole-card single file
    got = bd.download_via_browser(driver, url, timeout=25)
    if not got:
        return None
    got = Path(got)
    if not _looks_like_drf(got):
        try: got.unlink()
        except Exception: pass
        return None
    folder = FOLDER_FOR.get(code.upper(), code.upper())
    out_dir = Path(dest_base) / folder / "RAW_DATA" / "RACINGFORM" / f"{d.year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{code.upper()}{d:%m%d}.DRF"
    shutil.move(str(got), str(target))
    return target


def main():
    ap = argparse.ArgumentParser(description="BRISnet historical DRS downloader")
    ap.add_argument("--tracks", required=True, help="comma list of BRIS codes, e.g. DMR,GP,SA,CD")
    ap.add_argument("--start", help="YYYY-MM-DD (bulk mode)")
    ap.add_argument("--end", help="YYYY-MM-DD (bulk mode)")
    ap.add_argument("--dest", default=r"C:\Users\ryanr\Documents\BTSM")
    ap.add_argument("--probe", action="store_true", help="dump grid + try --test-dates only")
    ap.add_argument("--test-dates", default="", help="probe: comma ISO dates to try per track")
    ap.add_argument("--sleep", type=float, default=1.5, help="seconds between requests")
    a = ap.parse_args()
    codes = [c.strip().upper() for c in a.tracks.split(",") if c.strip()]

    driver = authenticate()
    try:
        live = visible_attrs(driver)
        log.info("[*] Visible tracks today (code: country/trackType/dayEvening — name):")
        for c, at in sorted(live.items()):
            log.info(f"      {c}: {at['country']}/{at['trackType']}/{at['dayEvening']} — {at['trackName']}")

        if a.probe:
            dates = [datetime.fromisoformat(x).date() for x in a.test_dates.split(",") if x.strip()]
            for c in codes:
                at = attrs_for(c, live)
                if not at:
                    log.warning(f"[probe] {c}: not carded today and no FALLBACK_ATTRS — "
                                f"copy its attrs from the grid above and re-run, or run when carded.")
                    continue
                for d in dates:
                    t = pull_one(driver, c, at, d, a.dest)
                    if t:
                        log.info(f"[probe] {c} {d}: OK -> {t}  ({t.stat().st_size} bytes)")
                    else:
                        log.info(f"[probe] {c} {d}: no file (dark day or not entitled)")
                    time.sleep(a.sleep)
            return

        if not (a.start and a.end):
            raise SystemExit("[!] bulk mode needs --start and --end (or use --probe)")
        start = datetime.fromisoformat(a.start).date()
        end = datetime.fromisoformat(a.end).date()
        summary = {}
        for c in codes:
            at = attrs_for(c, live)
            if not at:
                log.warning(f"[!] {c}: attrs unknown (not carded today, no fallback) — skipping.")
                continue
            hits = 0; first = None; last = None
            for d in daterange(start, end):
                t = pull_one(driver, c, at, d, a.dest)
                if t:
                    hits += 1; last = d; first = first or d
                    log.info(f"    {c} {d}: OK -> {t.name}")
                time.sleep(a.sleep)
            summary[c] = (hits, first, last)
            log.info(f"[*] {c}: {hits} cards  {first}..{last}")
        log.info("=" * 50)
        log.info("SUMMARY (cards pulled, date span):")
        for c, (h, f, l) in summary.items():
            log.info(f"  {c}: {h} cards  {f}..{l}")
    finally:
        try: driver.quit()
        except Exception: pass


if __name__ == "__main__":
    main()
