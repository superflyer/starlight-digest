# Starlight daily digest

Logs in to the Starlight Caregivers (ClearCare / WellSky) family room each
morning, scrapes care log entries from the dashboard feed, and emails a
structured digest — caregiver name, date, and log text — to a configured
recipient list.

Only entries posted since the last successful run are included.

Runs as a scheduled GitHub Actions job at 3:12 AM PT daily.

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

The workflow accepts two optional inputs for testing:

- **test_email** — override `EMAIL_TO` (e.g. `dfreeman@gmail.com`)
- **since_override** — ISO-8601 timestamp to override the default "since last
  successful run" window (e.g. `2026-05-10T00:00:00Z`)

If anything fails, the job uploads screenshots and an HTML dump of the page
as artifacts so you can see what the browser was looking at when it broke.
Failure also sends an email with the traceback.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

export PORTAL_EMAIL='...'
export PORTAL_PASSWORD='...'
export GMAIL_USER='...'
export GMAIL_APP_PASSWORD='...'
export EMAIL_TO='dfreeman@gmail.com'
export SINCE='2026-05-10T00:00:00Z'

python scraper.py
```

## How it works

1. Logs in to the ClearCare family-room portal via Playwright (headless
   Chromium).
2. Clicks "Load More Updates" on the dashboard to paginate all entries.
3. Parses each `article.postWrap` element from the Angular feed — extracts
   the caregiver name, date, and log text.
4. Filters out entries older than the `SINCE` timestamp (defaults to the
   last successful GitHub Actions run).
5. Emails the structured digest from "Starlight Digest Bot".

## Updating the selectors

If the login form or dashboard layout changes and the script breaks, the
fastest way to fix it is to record the working flow:

```bash
python -m playwright codegen https://starlightcaregivers.clearcareonline.com/family-room/login/
```

Do the login by hand in the recorder window. Codegen will print Python with the
exact selectors it observed; paste the relevant `page.fill(...)` and
`page.click(...)` lines into `do_login()`.

The dashboard feed entries use these selectors:

- `article.postWrap` — entry container (`ng-repeat="entry in feedEntries"`)
- `h3.userName` — posted-by name
- `span.timeStamp` — date (e.g. "Sun May 17, 2026")
- `div[ng-show*="model_type=='message'"] p.postMsg` — log text

## Schedule

The cron runs at `12 10 * * *` (10:12 UTC), which is:

- **3:12 AM PDT** (mid-March to early November)
- **2:12 AM PST** (rest of the year)
