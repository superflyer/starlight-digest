#!/usr/bin/env python3
"""
Starlight Caregivers / ClearCare family-room care-log scraper.

Logs in, extracts care log entries posted since the last successful run,
and emails a structured digest to the configured recipients.

Required env vars:
    PORTAL_EMAIL          login email for the family room
    PORTAL_PASSWORD       login password
    GMAIL_USER            sender Gmail address
    GMAIL_APP_PASSWORD    Gmail App Password (NOT your normal password)
    EMAIL_TO              comma-separated recipient list

Optional env vars:
    SINCE                 ISO-8601 timestamp — only include entries after this
                          (defaults to 24 hours ago)
"""
from __future__ import annotations

import html as html_mod
import os
import re
import smtplib
import ssl
import sys
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://starlightcaregivers.clearcareonline.com/family-room/login/"
ARTIFACTS_DIR = Path("artifacts")
ARTIFACTS_DIR.mkdir(exist_ok=True)

_shot = 0


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def screenshot(page, name: str) -> None:
    global _shot
    _shot += 1
    path = ARTIFACTS_DIR / f"{_shot:02d}-{name}.png"
    page.screenshot(path=str(path), full_page=True)
    log(f"Screenshot: {path}")


def dump_html(page, name: str) -> None:
    path = ARTIFACTS_DIR / f"{name}.html"
    path.write_text(page.content())
    log(f"HTML dump: {path}")


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"ERROR: required env var {name} not set")
    return value


def parse_since() -> datetime:
    raw = os.environ.get("SINCE", "")
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            log(f"Could not parse SINCE={raw!r}, defaulting to 24h ago")
    return datetime.now(timezone.utc) - timedelta(hours=24)


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def do_login(page, email: str, password: str) -> None:
    log(f"Navigating to {PORTAL_URL}")
    page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30_000)
    page.get_by_role("textbox", name="E-mail").fill(email)
    page.get_by_role("textbox", name="Password").fill(password)
    page.get_by_role("button", name="Login").click()
    page.wait_for_selector("#contentWrap", timeout=30_000)
    page.wait_for_load_state("networkidle", timeout=30_000)
    log(f"Logged in — {page.url}")
    screenshot(page, "after-login")


def load_all_updates(page) -> None:
    """Click 'Load More Updates' until all entries are visible."""
    clicks = 0
    while True:
        try:
            btn = page.get_by_text("Load More Updates", exact=False)
            if not btn.is_visible(timeout=2_000):
                break
            btn.click()
            page.wait_for_load_state("networkidle", timeout=15_000)
            clicks += 1
            log(f"Clicked 'Load More Updates' ({clicks})")
        except Exception:
            break
    log(f"Finished loading updates ({clicks} pagination clicks)")


def parse_entry_date(raw_date: str) -> datetime | None:
    """Parse a ClearCare timestamp like 'Sun May 17, 2026' into a datetime."""
    cleaned = raw_date.replace("CARE LOG,", "").strip()
    for fmt in ("%a %b %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_entries(page, since: datetime) -> list[dict]:
    """Extract care log entries from the dashboard feed.

    Each entry is an <article class="postWrap"> with:
      - h3.userName          — posted-by name (usually the agency contact)
      - span.timeStamp       — date string
      - p.postMsg.ng-binding — the actual log text (inside the 'message' div)
    """
    dump_html(page, "dashboard")

    articles = page.query_selector_all("article.postWrap")
    log(f"Found {len(articles)} article.postWrap elements")

    entries: list[dict] = []
    for article in articles:
        try:
            msg_el = article.query_selector(
                "div[ng-show*=\"model_type=='message'\"] p.postMsg"
            )
            if not msg_el:
                continue
            text = msg_el.inner_text().strip()
            if not text:
                continue

            name_el = article.query_selector("h3.userName")
            date_el = article.query_selector("span.timeStamp")

            name = name_el.inner_text().strip() if name_el else ""
            raw_date = date_el.inner_text().strip() if date_el else ""
            parsed_date = parse_entry_date(raw_date)

            if parsed_date and parsed_date < since:
                log(f"  Skipping entry dated {raw_date} (before SINCE)")
                continue

            # The log text often starts with "5/16, 10am to 4pm, Latu: ..."
            # Extract the caregiver name from the text itself
            caregiver = ""
            m = re.match(r"\d+/\d+,\s*[^,]+,\s*([^:]+):", text)
            if m:
                caregiver = m.group(1).strip()

            entries.append({
                "name": name,
                "date": raw_date,
                "caregiver": caregiver,
                "text": text,
            })
        except Exception as exc:
            log(f"  Error parsing entry: {exc}")

    log(f"Parsed {len(entries)} entries after date filter")
    return entries


def scrape_care_logs(email: str, password: str, since: datetime) -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            do_login(page, email, password)
            load_all_updates(page)
            screenshot(page, "all-updates-loaded")
            return parse_entries(page, since)
        except Exception:
            try:
                screenshot(page, "failure")
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def format_text(entries: list[dict], since: datetime) -> str:
    since_str = since.strftime("%B %d, %Y")
    lines = [f"Care log entries since {since_str}", "=" * 50]
    if not entries:
        lines.append("\nNo new entries found.")
    for e in entries:
        header = e.get("date", "")
        if e.get("caregiver"):
            header += f" — {e['caregiver']}"
        lines.append(f"\n{header}")
        lines.append("-" * len(header))
        lines.append(e["text"])
        lines.append("")
    return "\n".join(lines)


def format_html(entries: list[dict], since: datetime) -> str:
    since_str = since.strftime("%B %d, %Y")
    h = html_mod.escape
    parts = [
        "<html><body style='font-family:sans-serif;max-width:600px;margin:auto'>",
        "<h2 style='color:#333'>Care Log</h2>",
        f"<p style='color:#888;font-size:14px'>{len(entries)} entries since {h(since_str)}</p>",
    ]
    if not entries:
        parts.append("<p>No new entries found.</p>")
    for e in entries:
        caregiver = e.get("caregiver", "")
        date = h(e.get("date", ""))
        parts.append(
            "<div style='margin-bottom:24px;padding:12px 16px;"
            "border-left:4px solid #009688;background:#f9f9f9;"
            "border-radius:0 4px 4px 0'>"
        )
        parts.append(f"<div style='margin-bottom:8px'>")
        if caregiver:
            parts.append(f"<strong style='color:#009688'>{h(caregiver)}</strong>")
            parts.append(f" &mdash; <span style='color:#666'>{date}</span>")
        else:
            parts.append(f"<strong style='color:#666'>{date}</strong>")
        parts.append("</div>")
        parts.append(
            f"<div style='white-space:pre-wrap;line-height:1.5;font-size:14px'>"
            f"{h(e['text'])}</div>"
        )
        parts.append("</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def send_email(
    *,
    subject: str,
    body_text: str,
    body_html: str,
    gmail_user: str,
    gmail_app_pw: str,
    recipients: list[str],
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Starlight Digest Bot <{gmail_user}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls(context=ctx)
        server.login(gmail_user, gmail_app_pw)
        server.sendmail(gmail_user, recipients, msg.as_string())
    log(f"Sent '{subject}' to {recipients}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    portal_email = require_env("PORTAL_EMAIL")
    portal_password = require_env("PORTAL_PASSWORD")
    gmail_user = require_env("GMAIL_USER")
    gmail_app_pw = require_env("GMAIL_APP_PASSWORD")
    recipients = [r.strip() for r in require_env("EMAIL_TO").split(",") if r.strip()]

    since = parse_since()
    log(f"Filtering entries since {since.isoformat()}")

    today = datetime.now().strftime("%A, %B %d, %Y")
    subject = f"Starlight Care Log — {today}"

    try:
        entries = scrape_care_logs(portal_email, portal_password, since)
    except Exception as exc:
        err = (
            f"Care-log scrape failed.\n\n"
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        )
        log(err)
        send_email(
            subject=f"[FAILED] {subject}",
            body_text=err,
            body_html=f"<pre>{html_mod.escape(err)}</pre>",
            gmail_user=gmail_user,
            gmail_app_pw=gmail_app_pw,
            recipients=recipients,
        )
        return 1

    log(f"{len(entries)} entries to send")
    send_email(
        subject=subject,
        body_text=format_text(entries, since),
        body_html=format_html(entries, since),
        gmail_user=gmail_user,
        gmail_app_pw=gmail_app_pw,
        recipients=recipients,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
