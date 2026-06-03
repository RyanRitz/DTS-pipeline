"""
Scheduler diagnostic - shows what the pipeline WOULD do right now.
Safe to run any time. Does not modify state, write PDFs, or hit Equibase.
"""
import json
from datetime import datetime
import run_pipeline as rp


def main():
    now = datetime.now()
    today_str = now.date().strftime("%Y%m%d")

    print("=" * 70)
    print(f"  Scheduler dry-run  @  {now:%Y-%m-%d %H:%M:%S}")
    print(f"  Today (race date)   = {today_str}")
    print("=" * 70)

    # State file
    print("\n[1] STATE FILE")
    print("-" * 70)
    state = {}
    if rp.STATE_FILE.exists():
        try:
            state = json.loads(rp.STATE_FILE.read_text(encoding="utf-8"))
            today_pub = state.get("published", {}).get(today_str, {})
            if today_pub:
                print(f"  Today's state ({today_str}):")
                for track, entry in sorted(today_pub.items()):
                    preview = entry.get("preview", {})
                    final = entry.get("final", {})
                    anchors_done = entry.get("final_anchors_done", [])
                    if preview:
                        print(f"    {track}  PREVIEW  at {preview.get('published_at', '?')}")
                    if final:
                        print(f"    {track}  FINAL    at {final.get('published_at', '?')}  ({final.get('scratch_count', '?')} scratches)")
                    if anchors_done:
                        print(f"    {track}  anchors fired: {anchors_done}")
            else:
                print(f"  No publishes recorded for today.")
        except Exception as e:
            print(f"  ERROR reading state: {e}")
    else:
        print(f"  No state file (first run).")

    # DRFs
    print("\n[2] DRF FILES")
    print("-" * 70)
    drfs = rp.discover_drf_files()
    today_drfs = [d for d in drfs if d["race_date"] == today_str]
    print(f"  Total DRFs (post-whitelist): {len(drfs)}")
    print(f"  Today's DRFs: {len(today_drfs)}")

    if not today_drfs:
        print("\n  No DRFs for today - scheduler would do nothing.")
        return 0

    # Per-track decisions
    print("\n[3] PER-TRACK DECISIONS  (dry run)")
    print("-" * 70)

    for drf in sorted(today_drfs, key=lambda d: d["track"]):
        track = drf["track"]
        print(f"\n  -- {track} {drf['race_date']} --")

        try:
            fp = rp.get_first_post(drf["path"])
            if fp:
                mins = (fp - now).total_seconds() / 60.0
                fp_str = fp.strftime("%H:%M")
                print(f"     First post: {fp_str}  ({mins:+.0f} min from now)")
            else:
                print(f"     First post: could not parse")
        except Exception as e:
            print(f"     First post error: {e}")

        try:
            do_p, why_p = rp.should_publish_preview(drf, state)
            verdict = "WOULD FIRE" if do_p else "skip"
            print(f"     PREVIEW: {verdict}  -- {why_p}")
        except Exception as e:
            print(f"     PREVIEW: ERROR -- {e}")

        try:
            do_f, why_f = rp.should_publish_final(drf, state, now)
            verdict = "WOULD FIRE" if do_f else "skip"
            print(f"     FINAL:   {verdict}  -- {why_f}")
        except Exception as e:
            print(f"     FINAL:   ERROR -- {e}")

    print("\n" + "=" * 70)
    print("  Done. Nothing was fetched, written, or modified.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
