# BTSM Desktop Production Migration Checklist

**Goal:** Move the daily handicapping pipeline from your laptop (dev) to your home desktop (production runner). Laptop stays where you write and test edits; the desktop runs the blessed code on a schedule and tells you how it went. You almost never sit at it.

**How to use this:** Work top to bottom. Each box is a real step. When you hit one you want me to do — write a script, generate a `requirements.txt`, draft the email-on-finish code, etc. — paste back where you are and I'll produce it. Phases 1–4 are setup-once; Phases 5–9 are the parts that make a headless box livable.

**A few decisions baked in (confirm or change these):**
- The desktop runs **only the Python pipeline**. The web layer (Next.js/Supabase) stays on Vercel/cloud — the desktop just uploads PDFs to it, so **Node.js is not required** unless you decide to develop the web layer here too.
- Notifications go to **ryanritz1@gmail.com** via Gmail SMTP (needs an app password — covered in Phase 6).
- Source of truth is **Git**. Pushing from the laptop is how you deploy. The desktop pulls automatically at the start of each run.

---

## Phase 0 — Before you leave the laptop (do this first)

- [ ] Confirm the project is a Git repo and everything is committed: `git status` should be clean.
- [ ] Make sure there's a `requirements.txt` (or `pyproject.toml`) listing every Python package the pipeline needs. *(If you don't have one, tell me and I'll generate it from the imports — this is the thing most likely to bite you on the new machine.)*
- [ ] Write down the exact Python version your laptop uses: `python --version`. You want to match the major.minor on the desktop.
- [ ] Make a list of every secret/credential the pipeline uses: BRISnet login, AMWager/Equibase API key, website upload creds (SFTP/API), and a Gmail app password (you'll create that in Phase 6). **Do not commit these.** You'll re-enter them on the desktop in Phase 4.
- [ ] Confirm your Git remote is reachable from both machines (GitHub/GitLab/etc.): `git remote -v`. *(If the repo only lives on the laptop, we need to fix that first — a remote is what makes the laptop→desktop handoff work.)*

---

## Phase 1 — Core installs on the desktop

Walk over to the desktop (or RDP in once) for this phase.

- [ ] **Python** — install the same major.minor as the laptop from python.org. **Check "Add Python to PATH"** during install. Verify: `python --version`.
- [ ] **Git** — install from git-scm.com (includes Git Bash, handy). Verify: `git --version`.
- [ ] **A code editor** — VS Code (you'll also use it for Remote-SSH from the laptop later, Phase 5). Optional on the desktop itself but harmless to have.
- [ ] **Set power settings so the machine never sleeps** — Settings → System → Power → Screen and sleep → set sleep to **Never** (at least when plugged in). A sleeping desktop misses its scheduled run. Also disable "fast startup" if it causes issues.
- [ ] *(Optional, skip unless developing the web layer here)* Node.js LTS.

---

## Phase 2 — Get the code onto the desktop

- [ ] Choose a stable home for production, e.g. `C:\BTSM\production\`. Keep it out of OneDrive/Documents-synced folders to avoid sync conflicts on files the pipeline writes.
- [ ] `git clone <your-remote-url> C:\BTSM\production`
- [ ] Confirm you're on the right branch (`git branch`) — production should track `main` (or whatever your release branch is).

---

## Phase 3 — Python environment

- [ ] Create a virtual environment inside the project: `python -m venv .venv`
- [ ] Activate it: `.venv\Scripts\activate`
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Smoke-test imports — run a quick `python -c "import pandas, reportlab, requests"` (adjust to your actual libs). If anything errors, that's a missing line in `requirements.txt`.
- [ ] If the pipeline uses **Selenium/Playwright** for BRISnet downloads, install the browser driver too (`playwright install`, or the matching ChromeDriver). This is a common thing to forget on a fresh box.

---

## Phase 4 — Secrets & config (the part that's bitten this project before)

Secrets live **only on the desktop**, never in Git.

- [ ] Create a `.env` file in the project root with your credentials (BRISnet, API key, upload creds, Gmail app password placeholder for now).
- [ ] **Confirm `.env` is in `.gitignore`** before you ever commit again. Verify with `git status` — `.env` must NOT appear as a tracked/staged file. *(This project has had plaintext secrets slip in twice; this checkbox is the guardrail.)*
- [ ] Verify the pipeline reads from the `.env` (or Windows environment variables) rather than hardcoded values. If anything is still hardcoded on the laptop, fix it before migrating — tell me and I'll help refactor it to read from env.
- [ ] Re-create any non-secret config files the pipeline expects (paths, track whitelist, output folders). Adjust any laptop-specific absolute paths to the desktop's layout.

---

## Phase 5 — Remote access (so you can reach in without sitting there)

- [ ] **Tailscale** — install on *both* laptop and desktop, sign in with the same account. This puts both machines on a private network reachable from anywhere, no port forwarding, no exposing the desktop to the internet. Note the desktop's Tailscale name/IP.
- [ ] **OpenSSH Server on the desktop** — Settings → System → Optional features → Add → "OpenSSH Server". Then start it and set it to auto-start (Services → "OpenSSH SSH Server" → Startup type: Automatic). Test from the laptop: `ssh <you>@<desktop-tailscale-name>`.
- [ ] **VS Code Remote-SSH from the laptop** — install the "Remote - SSH" extension, then "Connect to Host" using the desktop's Tailscale name. Now you can open/edit files and run a terminal on the desktop from your laptop, lag-free. This is your tool for quick fixes and log-poking.
- [ ] **RDP** — confirm Remote Desktop is enabled on the desktop (Settings → System → Remote Desktop → On). Over Tailscale you can RDP in for the GUI-only stuff: Task Scheduler tweaks, eyeballing that PDFs got produced. *(Note: Windows Home edition can't host RDP — if that's your edition, use VS Code Remote-SSH for everything and walk over for the rare GUI task, or we look at alternatives.)*

---

## Phase 6 — Make the run script production-ready

Two additions turn `run_daily.py` from a script into a headless production job. *(I can write both of these for you — just share your current `run_daily.py` or its entry point.)*

- [ ] **Pull latest code at the start of every run.** Wrap the job in a small batch file that Task Scheduler calls:
  - `git pull` (so pushing from your laptop = deploying)
  - activate `.venv`
  - run `run_daily.py`
  This means you never manually sync the desktop.
- [ ] **Email-on-finish (success AND failure).** At the end of the run, send a one-line result to ryanritz1@gmail.com: which tracks/dates were produced, or the error + traceback if it blew up. On a box you don't watch, **silence should mean "something's wrong," not "probably fine."**
  - [ ] Create a **Gmail app password** (Google Account → Security → 2-Step Verification → App passwords). Put it in `.env`. Don't use your real Gmail password.
- [ ] **Logging to a file.** Have the run append to a dated log in a `logs\` folder so when something fails you can SSH in and read exactly what happened.
- [ ] *(Nice-to-have)* Wrap the main steps in try/except so one failed track doesn't kill the whole run, and the email tells you which step failed.

---

## Phase 7 — Schedule it

- [ ] Open **Task Scheduler** → Create Task (not "Basic Task" — you need the extra options).
- [ ] **Action:** start the batch file from Phase 6 (the one that does git pull → activate → run).
- [ ] **Trigger:** daily, at a time after BRISnet PP files and scratch/condition data are reliably available. *(Pick this based on when your data sources update — tell me your data timing and I'll suggest a run time with buffer.)*
- [ ] **Settings to check:** "Run whether user is logged on or not," "Run with highest privileges," and "Wake the computer to run this task" (pairs with the no-sleep setting). Set "Start the task only if the computer is on AC power" appropriately for a desktop.
- [ ] Set **"Stop the task if it runs longer than"** to a sane ceiling so a hung browser-automation step doesn't run forever.

---

## Phase 8 — First supervised production run

- [ ] Run the Task Scheduler job **manually** ("Run" in the right-click menu) while you're watching, before trusting it overnight.
- [ ] Confirm end to end: code pulled → data downloaded → scoring ran → PDFs generated in the right place → PDFs uploaded to the website → **email arrived** → log written.
- [ ] Compare a desktop-produced PDF against a known-good laptop one for the same card — make sure scoring output matches exactly. *(This is the real proof the migration worked, not just that the script didn't crash.)*
- [ ] Let it run on schedule one night, then check the morning email.

---

## Phase 9 — Your ongoing edit workflow (the everyday loop)

Once you're live, "make a small edit" looks like this:

1. Edit and test on the **laptop**.
2. `git commit` + `git push`.
3. Done — the desktop's next scheduled run does `git pull` and picks it up.

For anything you need to do *on the desktop directly:*
- Quick code fix or reading a log right now → **VS Code Remote-SSH** from the laptop.
- Task Scheduler change or visually checking output → **RDP** over Tailscale.
- Force an immediate run to test a fix → right-click the task → Run (via RDP), or run the batch file over SSH.

**Rule of thumb:** if you find yourself editing files *directly on the desktop* for anything but an emergency, commit those changes back to Git right away — otherwise the laptop and desktop drift apart and your next push overwrites the desktop fix.

---

## Where I can jump in

Tell me when you reach any of these and I'll produce it on the spot:
- Generate `requirements.txt` from your actual imports (Phase 0/3)
- The git-pull → activate → run **batch wrapper** (Phase 6)
- The **email-on-finish + logging** code added to `run_daily.py` (Phase 6)
- Refactor any hardcoded secrets to read from `.env` (Phase 4)
- A suggested **run time** once you tell me your data-availability timing (Phase 7)
