BTSM-DTS-Cleanup — Task Scheduler Setup
========================================

What this task does
-------------------
Once a day at 3:00 AM Eastern, runs `cleanup_dts.py` to enforce the
3-day rolling archive on DTS. Anything in the `sheets` table with
race_date older than today minus 3 days gets its blob deleted from
Vercel Blob and its row removed from Supabase.

Failure handling has two layers:
  1. cleanup_dts.py fires its own granular alerts (401, 5xx, timeout,
     partial blob failures) — same notify.py path your other tasks use
  2. run_cleanup.bat fires a catastrophic-failure email only if Python
     itself couldn't start (missing from PATH, script deleted, etc.)

So you get exactly one email per problem — no duplicates.

────────────────────────────────────────────────────────────
Step 1 — Drop the files into FullAutomation
────────────────────────────────────────────────────────────

Save these two files to:
    C:\Users\ryanr\Documents\BTSM\FullAutomation\

    run_cleanup.bat
    BTSM-DTS-Cleanup.xml

(cleanup_dts.py + test_cleanup_dts.py should already be there from step 2.)

────────────────────────────────────────────────────────────
Step 2 — Test the .bat manually
────────────────────────────────────────────────────────────

From a cmd window in the FullAutomation folder:

    run_cleanup.bat

Expected: same output as `python cleanup_dts.py` directly — should print
the cutoff date and "OK ... blobs_deleted=0 rows_deleted=0".

If it works, you're ready to schedule it.

────────────────────────────────────────────────────────────
Step 3 — Import the Task Scheduler XML
────────────────────────────────────────────────────────────

1. Open Task Scheduler (Start → type "Task Scheduler" → Enter,
   or run `taskschd.msc`).

2. In the left pane, navigate into the `BTSM` folder (where your other
   tasks live — `BTSM-Daily-Download` and `BTSM-Pipeline-Poller`).

3. Click into the BTSM folder.

4. Action menu (top) → "Import Task..."

5. Browse to:
       C:\Users\ryanr\Documents\BTSM\FullAutomation\BTSM-DTS-Cleanup.xml

6. The task properties dialog opens. Verify:

    - Name:        BTSM-DTS-Cleanup
    - Description: Daily 3 AM Eastern: enforce 3-day rolling archive...
    - Triggers:    Daily at 3:00 AM (the XML has -04:00 = US Eastern)
    - Actions:     Start a program → run_cleanup.bat
    - Principal:   "Run only when user is logged on" (InteractiveToken)
                   "Run with highest privileges" should be UNCHECKED
                   (matches the LeastPrivilege setting)

7. Click OK to save.

   If Windows prompts for the password of the running user, enter your
   Windows login password. (This only happens if you change "Run whether
   user is logged on or not" — which I'd leave on InteractiveToken so it
   only runs when you're signed in.)

────────────────────────────────────────────────────────────
Step 4 — Verify the task is there
────────────────────────────────────────────────────────────

In the BTSM folder you should now see three tasks:

    BTSM-Daily-Download
    BTSM-DTS-Cleanup        ← new
    BTSM-Pipeline-Poller

Right-click `BTSM-DTS-Cleanup` → Run.

This triggers it immediately (without waiting for 3 AM). Watch:

    - History tab          should show "Task started" → "Task completed"
    - Vercel function logs should show a new POST /api/sheets/cleanup
    - Your Gmail            no new emails (clean run → no alerts)

If you got that, the task is live.

────────────────────────────────────────────────────────────
Step 5 — Validate against the design doc (§9.3)
────────────────────────────────────────────────────────────

The design doc specifies one validation:

    "Manually insert a row in sheets with an old race_date;
     run the cleanup job; confirm the blob and row are gone."

To do this:

1. Use the Supabase dashboard → Table Editor → sheets → Insert row

   Set these fields:
     filename:        20260101-ZZZ-FINAL.pdf
     track:           ZZZ
     race_date:       2026-01-01
     label:           FINAL
     blob_url:        https://example.com/fake-url   (won't actually be deleted
                                                       because no real blob exists;
                                                       blob_failures will be 1)
     uploaded_at:     2026-01-01T12:00:00Z

2. Trigger the task: right-click BTSM-DTS-Cleanup → Run.

3. Check the response. It should report 1 blob_failure (the fake URL)
   and 0 rows_deleted (the row stays because its blob couldn't be deleted).

4. Now insert a *real* old sheet via the upload endpoint:

    curl.exe -i -X POST https://downthestretch.ai/api/sheets/upload `
      -H "X-DTS-Upload-Secret: $secret" `
      -F "track=ZZZ" `
      -F "race_date=2026-01-01" `
      -F "label=FINAL" `
      -F "file=@output\SOME_EXISTING.pdf"

   Then trigger cleanup again. Now you should see blobs_deleted=1,
   rows_deleted=1. Verify the row is gone in Supabase. Done.

────────────────────────────────────────────────────────────
Done
────────────────────────────────────────────────────────────

Phase 3 is complete when:
  ☐ Manual `run_cleanup.bat` exits clean
  ☐ Task imports successfully into BTSM folder
  ☐ Right-click → Run produces a clean response in Vercel logs
  ☐ §9.3 validation: an old row gets swept on the next run

After that the task is on autopilot. Daily at 3 AM Eastern, anything
older than 3 days gets removed without you touching it.
