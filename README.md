# Starlight daily digest

Logs in to the Starlight Caregivers family room each morning, scrapes the
dashboard, and emails the contents to a configured recipient list.

Runs as a scheduled GitHub Actions job.

## One-time setup

### 1. Create a private repo

This repo contains login credentials in its Actions secrets, so it must be
private. Push these files to it.

### 2. Generate a Gmail App Password

App Passwords require 2-Step Verification on the sender account.

1. Enable 2-Step Verification: <https://myaccount.google.com/security>
2. Create an App Password: <https://myaccount.google.com/apppasswords>
   - Pick "Mail" as the app, or use the custom name field ("Starlight digest").
   - Save the 16-character password Google shows — you can't view it again.

### 3. Add repo secrets

Repo → Settings → Secrets and variables → Actions → New repository secret.
Add all five:

| Name                 | Value                                              |
|----------------------|----------------------------------------------------|
| `PORTAL_EMAIL`       | login email for the Starlight family room          |
| `PORTAL_PASSWORD`    | login password                                     |
| `GMAIL_USER`         | sender Gmail address (e.g. `dfreeman@gmail.com`)   |
| `GMAIL_APP_PASSWORD` | the 16-char App Password from step 2 (no spaces)   |
| `EMAIL_TO`           | `dfreeman@gmail.com,laura.f.gordon@gmail.com`      |

### 4. Test it

Repo → Actions tab → "Daily Starlight digest" → "Run workflow".

This triggers a manual run. The scraper still does its random jitter sleep, so
the run will take up to an hour unless you set `SKIP_JITTER=1` (which you can
add as a temporary repo variable, or test locally instead — see below).

If anything fails, the job uploads `artifacts/failure.png` so you can see what
the browser was looking at when it broke. Failure also sends an email with the
traceback.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

export PORTAL_EMAIL='...'
export PORTAL_PASSWORD='...'
export GMAIL_USER='...'
export GMAIL_APP_PASSWORD='...'
export EMAIL_TO='dfreeman@gmail.com,laura.f.gordon@gmail.com'
export SKIP_JITTER=1

python scraper.py
```

## Updating the selectors

If the login form or dashboard layout changes and the script breaks, the
fastest way to fix it is to record the working flow:

```bash
python -m playwright codegen https://starlightcaregivers.clearcareonline.com/family-room/login/
```

Do the login by hand in the recorder window. Codegen will print Python with the
exact selectors it observed; paste the relevant `page.fill(...)` and
`page.click(...)` lines into `scrape_dashboard()`.

## Notes on the schedule

GitHub Actions cron is UTC-only and does not follow DST. `0 10 * * *` means:

- 3:00 AM PDT (mid-March to early November) — script jitters to 3:00–4:00 AM
- 2:00 AM PST (rest of the year) — script jitters to 2:00–3:00 AM

If you'd rather hold the PST window steady at 3–4 AM, change the cron to
`0 11 * * *` and accept that PDT runs become 4–5 AM. There is no way to get
both halves of the year into the same PT clock window with a single cron.

GitHub also notes that scheduled jobs may be delayed by a few minutes during
heavy load, so don't pick a time where precision matters.
