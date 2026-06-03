Phase 2 — DTS Publishing Integration: Deployment Steps
========================================================

Goal: replace upload_to_btsm() stub with real DTS upload.

────────────────────────────────────────────────────────────
1.  Drop new files into FullAutomation\
────────────────────────────────────────────────────────────

Save these three files to:
    C:\Users\ryanr\Documents\BTSM\FullAutomation\

    upload_to_dts.py
    test_upload_to_dts.py

────────────────────────────────────────────────────────────
2.  Append the env vars
────────────────────────────────────────────────────────────

Open .env in the FullAutomation folder.
Append the contents of env_upload_additions.txt.
Replace the placeholder DTS_UPLOAD_SECRET value with the real
secret from your text file (same one you put in Vercel).

────────────────────────────────────────────────────────────
3.  Run the unit tests — should all pass before touching run_pipeline
────────────────────────────────────────────────────────────

    cd C:\Users\ryanr\Documents\BTSM\FullAutomation
    python test_upload_to_dts.py

Expected: "All 11 tests passed"

────────────────────────────────────────────────────────────
4.  Live dry-run against the real endpoint
────────────────────────────────────────────────────────────

Find any old PDF in the output folder (or use the test PDF you
uploaded by curl during Phase 1). Then:

    python upload_to_dts.py output\some_existing.pdf ^
        --track LRL --race-date 2026-05-15 --dry-run

Expected: prints "DRY RUN — would POST" with the fields shown.
No HTTP call is made.

────────────────────────────────────────────────────────────
5.  Live test against the real endpoint  (small one-off)
────────────────────────────────────────────────────────────

Use a non-conflicting label like a test PREVIEW for a track
you're not running today. Drop --dry-run:

    python upload_to_dts.py output\some_existing.pdf ^
        --track ZZZ --race-date 2026-05-15

Expected:
    [upload_to_dts] OK   20260515-ZZZ-PREVIEW.pdf  →  https://...

Then verify in Supabase: row in `sheets` table.
And in Vercel Blob: blob exists.
And in browser: https://downthestretch.ai/sheets/20260515-ZZZ-PREVIEW.pdf
(may require entering the listing-page password first).

If anything fails, check the email — upload_to_dts fires Gmail
alerts on every failure via the same notify.py path.

────────────────────────────────────────────────────────────
6.  Patch run_pipeline.py
────────────────────────────────────────────────────────────

Back up first:

    copy run_pipeline.py run_pipeline.py.bak

Apply the three edits in run_pipeline_PATCH.txt.
Verify with:

    python -c "import ast; ast.parse(open('run_pipeline.py').read()); print('OK')"
    findstr /N upload_to_btsm run_pipeline.py
    findstr /N upload_to_dts  run_pipeline.py

The btsm findstr should return nothing.
The dts findstr should return 3 hits (1 import + 2 call sites).

────────────────────────────────────────────────────────────
7.  Pipeline dry tick
────────────────────────────────────────────────────────────

If today is a race day, the next scheduled poller tick will
exercise the integration. If not, you can wait for the next
race day to confirm end-to-end — Phase 2 is mechanically done
at this point.

If you want immediate confirmation, run a manual tick:

    python run_pipeline.py

Pick a track that's already published today (so PREVIEW is
skipped) and watch for FINAL to fire inside an anchor window
— or just inspect the logs to confirm the new upload_to_dts
line appears where the old [stub] upload_to_btsm line used to.

────────────────────────────────────────────────────────────
Done.
────────────────────────────────────────────────────────────

Phase 2 is complete when:
  ☐ test_upload_to_dts.py passes 11/11
  ☐ A manual upload via the CLI lands in Supabase + Vercel Blob
  ☐ run_pipeline.py imports cleanly and findstr confirms 3 hits
  ☐ One live pipeline tick auto-publishes a sheet
