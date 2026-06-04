# BTSM Desktop Production Migration Checklist

**Goal:** Move the daily handicapping pipeline from the laptop (dev) to the home desktop (production runner). Laptop stays where edits are written and tested; the desktop runs the blessed code on a schedule and reports how it went. You rarely sit at it.

---

## CURRENT STATUS (as of this hand-off)

- **Phase 0 — DONE.** Pipeline is under Git, pushed to **https://github.com/RyanRitz/DTS-pipeline** (branch `main`). GitHub user: RyanRitz. The `dts-web` web layer is a *separate* repo (github.com/RyanRitz/dts-web).
- **Phases 1–2 — effectively done.** Python 3.13.x and Git are installed on the desktop; the pipeline code is present and the downloader has been fixed/running.
- **Phases 3–4 — in progress / mostly done.** Dependencies installed; `.env` recreated enough that the downloader works. Still verify: `playwright install` ran, and `.env` has *all* creds (BRISnet, upload/SFTP, and a Gmail app password for notifications).
- **Phases 5–9 — NOT STARTED.** Remote access, the headless run wrapper, scheduling, supervised first run, and the ongoing edit loop.
- **Next real milestone:** Phase 6 — the production run wrapper (git pull → run → email-on-finish → log), since a headless box must announce its own failures.
- **Known cleanup:** the desktop folder may be a manual copy rather than a clean `git clone` (this file was missing). Worth re-syncing the desktop to the repo so laptop and desktop match. Also a stale README named the wrong downloader — fix or delete the dead downloader file.

**Laptop Python = 3.13.13 → install 3.13.x on desktop. Lockfile: `requirements.lock.txt` in the repo.**

---

## Phase 0 — Version control (DONE)

- [x] Project is a Git repo, everything committed, `.env` + `brisnet_cookies.json` gitignored.
- [x] `requirements.txt` (human list) and `requirements.lock.txt` (exact pinned versions) committed.
- [x] Private GitHub repo created and pushed; both machines can reach it.

---

## Phase 1 — Core installs on the desktop

- [ ] **Python 3.13.x** from python.org — check "Add Python to PATH". Verify: `python --version`.
- [ ] **Git** from git-scm.com. Verify: `git --version`.
- [ ] **VS Code** (also used for Remote-SSH from the laptop in Phase 5).
- [ ] **Never sleep:** Settings → System → Power → Sleep = Never (plugged in). Disable fast startup if it interferes. A sleeping desktop misses its scheduled run.
- [ ] *(Optional, only if developing the web layer here)* Node.js LTS.

---

## Phase 2 — Get the code onto the desktop

- [ ] Stable production home, e.g. `C:\BTSM\production\`. Keep it OUT of OneDrive/synced folders (the pipeline writes files; sync will fight it).
- [ ] `git clone https://github.com/RyanRitz/DTS-pipeline.git C:\BTSM\production`
- [ ] Confirm branch: `git branch` → should be `main`.
- [ ] **If the desktop already has a hand-copied folder:** don't mix it with the clone. Either clone fresh and re-add the gitignored secrets, or `cd` into the existing folder and run `git init` + `git remote add` + `git fetch` + `git reset --hard origin/main` to align it to the repo (this overwrites local code with the repo version — back up first if unsure).

---

## Phase 3 — Python environment

- [ ] Create a venv in the project: `python -m venv .venv`
- [ ] Activate: `.venv\Scripts\activate`
- [ ] Install exact versions: `pip install -r requirements.lock.txt`
- [ ] **`playwright install`** — downloads the browser binaries the BRISnet downloader drives. Fresh machines don't have these; this is a common "downloader is broken" cause.
- [ ] If Selenium is used, make sure Chrome is installed (webdriver-manager fetches the driver, but needs a browser).
- [ ] Smoke test: `python -c "import pandas, weasyprint, selenium, playwright, pyreadstat"` — any error = missing dep.

---

## Phase 4 — Secrets & config (gitignored — must be recreated on desktop)

- [ ] Create `.env` in the project root with every credential: BRISnet login, upload/SFTP creds (`DTS_UPLOAD_URL` / `DTS_UPLOAD_SECRET` / `DTS_CLEANUP_URL`), and a Gmail app password (Phase 6).
- [ ] Confirm `.env` is gitignored: `git status` must NOT list it. (This project has had plaintext secrets slip into Git before — keep that wall up.)
- [ ] `brisnet_cookies.json` — the downloader will regenerate it on a successful login, or copy it over from the laptop once.
- [ ] Adjust any laptop-specific absolute paths in `config.py` to the desktop's folder layout.

---

## Phase 5 — Remote access (edit & check without sitting at the desktop)

- [ ] **Tailscale** on BOTH laptop and desktop, same account → private network reachable anywhere, no port forwarding. Note the desktop's Tailscale name.
- [ ] **OpenSSH Server** on the desktop: Settings → Optional features → add "OpenSSH Server"; set the service to start automatically. Test from laptop: `ssh <you>@<desktop-tailscale-name>`.
- [ ] **VS Code Remote-SSH** from the laptop ("Remote - SSH" extension → Connect to Host). Now you edit files / run a terminal on the desktop, lag-free. Primary tool for quick fixes.
- [ ] **RDP** for GUI-only tasks (Task Scheduler, eyeballing output). Enable in Settings → System → Remote Desktop. *(Windows Home can't host RDP — if so, use Remote-SSH for everything and walk over for the rare GUI task.)*

---

## Phase 6 — Make the run script production-ready (the meat)

- [ ] **Pull latest at the start of every run.** A batch wrapper that Task Scheduler calls: `git pull` → activate `.venv` → run the pipeline. So pushing from the laptop = deploying.
- [ ] **Email-on-finish (success AND failure)** to ryanritz1@gmail.com — which tracks/dates produced, or the error + traceback. On a box you don't watch, silence must mean "broken," not "fine."
  - [ ] Create a **Gmail app password** (Google Account → Security → 2-Step Verification → App passwords) and put it in `.env`. Not your real password.
  - [ ] Note: there's already a `notify.py` in the project — reuse/extend it rather than rebuilding.
- [ ] **Logging to a dated file** in a `logs\` folder so failures can be read over SSH.
- [ ] *(Nice-to-have)* try/except around each track so one bad card doesn't kill the whole run, and the email says which step failed.

---

## Phase 7 — Schedule it

- [ ] Task Scheduler → Create Task (not "Basic Task").
- [ ] **Action:** run the Phase 6 batch wrapper.
- [ ] **Trigger:** daily, after BRISnet PP files + scratch/condition data are reliably available (pick the time from your data timing, add buffer).
- [ ] **Settings:** "Run whether user is logged on or not," "Run with highest privileges," "Wake the computer to run this task." Set a max run time so a hung browser step can't run forever.

---

## Phase 8 — First supervised production run

- [ ] Run the scheduled task manually ("Run") while watching.
- [ ] Confirm end-to-end: code pulled → data downloaded → scoring ran → PDFs generated → uploaded to the site → **email arrived** → log written.
- [ ] Compare a desktop-produced PDF against a known-good laptop one for the same card — scoring output must match. This is the real proof, not just "it didn't crash."
- [ ] Let it run once on schedule overnight, then check the morning email.

---

## Phase 9 — Ongoing edit workflow

1. Edit and test on the **laptop**.
2. `git commit` + `git push`.
3. Desktop's next run does `git pull` and picks it up.

For direct desktop work: quick code/log → VS Code Remote-SSH; Task Scheduler/visual checks → RDP over Tailscale; force a test run → run the batch file over SSH or via the scheduler.

**Rule:** if you ever edit files directly on the desktop, commit them back to Git right away, or the two machines drift and your next push overwrites the desktop fix.

---

## Where Claude can jump in

- The git-pull → activate → run **batch wrapper** (Phase 6)
- **Email-on-finish + logging** wired into the run (Phase 6, building on `notify.py`)
- Re-syncing the desktop folder to match the repo (Phase 2)
- A suggested **run time** given your data-availability timing (Phase 7)
