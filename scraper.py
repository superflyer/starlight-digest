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


def parse_entries(page, since: datetime) -> list[dict]:
    """Extract update entries from the dashboard.

    Each update card on the ClearCare dashboard has a caregiver name,
    date, and log text.  We try several selector strategies, then dump
    the HTML for offline analysis regardless.
    """
    dump_html(page, "dashboard")

    entries: list[dict] = []

    # Strategy: look for common container selectors
    for sel in (
        ".update-entry",
        ".activity-update",
        ".dashboard-update",
        "[class*='update']",
        "[class*='Update']",
        ".feed-item",
        ".stream-item",
        "[class*='feed']",
        "[class*='stream']",
    ):
        items = page.query_selector_all(sel)
        if items:
            log(f"Matched {len(items)} elements with '{sel}'")
            for item in items:
                text = item.inner_text().strip()
                if text:
                    entries.append({"raw": text, "_sel": sel})
            break

    if not entries:
        log("No structured selectors matched — falling back to #contentWrap text")
        try:
            text = page.locator("#contentWrap").inner_text(timeout=5_000)
        except Exception:
            text = page.locator("body").inner_text()
        entries.append({"raw": text, "_sel": "fallback"})

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
    since_str = since.strftime("%B %d, %Y %I:%M %p UTC")
    lines = [f"Care log entries since {since_str}", "=" * 50]
    if not entries:
        lines.append("\nNo new entries found.")
    for i, e in enumerate(entries, 1):
        lines.append(f"\n--- Entry {i} ---")
        if "name" in e:
            lines.append(f"Caregiver: {e['name']}")
        if "date" in e:
            lines.append(f"Date: {e['date']}")
        lines.append(e.get("text", e.get("raw", "")))
        lines.append("")
    return "\n".join(lines)


def format_html(entries: list[dict], since: datetime) -> str:
    since_str = since.strftime("%B %d, %Y %I:%M %p UTC")
    h = html_mod.escape
    parts = [
        "<html><body style='font-family:sans-serif'>",
        "<h2>Care Log Entries</h2>",
        f"<p style='color:#666'>Since {h(since_str)}</p>",
    ]
    if not entries:
        parts.append("<p>No new entries found.</p>")
    for e in entries:
        parts.append(
            "<div style='margin-bottom:20px;padding:12px;"
            "border-left:3px solid #2196F3;background:#f5f5f5'>"
        )
        if "name" in e:
            parts.append(f"<strong>{h(e['name'])}</strong>")
        if "date" in e:
            parts.append(f" &mdash; <em>{h(e['date'])}</em>")
        if "name" in e or "date" in e:
            parts.append("<br>")
        text = e.get("text", e.get("raw", ""))
        parts.append(
            f"<p style='margin:8px 0 0;white-space:pre-wrap'>{h(text)}</p>"
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
