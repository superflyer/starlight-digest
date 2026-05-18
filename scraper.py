#!/usr/bin/env python3
"""
Starlight Caregivers / ClearCare family-room daily dashboard scraper.

Logs in, captures the dashboard contents, and emails a digest to the configured
recipients. Designed to run as a GitHub Actions cron job, but works fine locally
too — just set the env vars below.

Required env vars:
    PORTAL_EMAIL          login email for the family room
    PORTAL_PASSWORD       login password
    GMAIL_USER            sender Gmail address
    GMAIL_APP_PASSWORD    Gmail App Password (NOT your normal password)
    EMAIL_TO              comma-separated recipient list

Optional env vars:
    SKIP_JITTER=1         skip the random pre-run sleep (useful for local testing)
"""
from __future__ import annotations

import os
import random
import smtplib
import ssl
import sys
import time
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright


PORTAL_URL = "https://starlightcaregivers.clearcareonline.com/family-room/login/"
ARTIFACTS_DIR = Path("artifacts")
ARTIFACTS_DIR.mkdir(exist_ok=True)


def log(msg: str) -> None:
    print(f"[{datetime.utcnow().isoformat(timespec='seconds')}Z] {msg}", flush=True)


def jitter_sleep(max_seconds: int = 3600) -> None:
    """Sleep a random 0–max_seconds so the job doesn't run at a predictable minute."""
    if os.environ.get("SKIP_JITTER") == "1":
        log("SKIP_JITTER set, skipping pre-run sleep")
        return
    delay = random.randint(0, max_seconds)
    log(f"Sleeping {delay}s before run")
    time.sleep(delay)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"ERROR: required env var {name} not set")
    return value


def scrape_dashboard(email: str, password: str) -> tuple[str, str]:
    """Returns (plain_text, html) of the dashboard. Raises on failure."""
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
            log(f"Navigating to {PORTAL_URL}")
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30_000)

            # Login form. Selectors captured via `playwright codegen` against the
            # live portal. If these break, re-run codegen and update.
            page.get_by_role("textbox", name="E-mail").fill(email)
            page.get_by_role("textbox", name="Password").fill(password)
            page.get_by_role("button", name="Login").click()

            # Wait for the dashboard's main content wrapper to appear -- this is
            # our signal that login succeeded and the page has rendered.
            page.wait_for_selector("#contentWrap", timeout=30_000)
            page.wait_for_load_state("networkidle", timeout=30_000)
            log(f"Logged in. Current URL: {page.url}")

            # Grab the dashboard content.
            dashboard_html = page.content()
            try:
                dashboard_text = page.locator("#contentWrap").inner_text(timeout=5_000)
            except Exception:
                dashboard_text = page.locator("body").inner_text()

            page.screenshot(
                path=str(ARTIFACTS_DIR / "dashboard.png"), full_page=True
            )
            log("Captured dashboard text + screenshot")
            return dashboard_text, dashboard_html

        except Exception:
            try:
                page.screenshot(
                    path=str(ARTIFACTS_DIR / "failure.png"), full_page=True
                )
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


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
    msg["From"] = gmail_user
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


def main() -> int:
    portal_email = require_env("PORTAL_EMAIL")
    portal_password = require_env("PORTAL_PASSWORD")
    gmail_user = require_env("GMAIL_USER")
    gmail_app_pw = require_env("GMAIL_APP_PASSWORD")
    recipients = [r.strip() for r in require_env("EMAIL_TO").split(",") if r.strip()]

    jitter_sleep(3600)

    today = datetime.now().strftime("%A, %B %d, %Y")
    subject = f"Starlight Caregivers daily update — {today}"

    try:
        text, html = scrape_dashboard(portal_email, portal_password)
    except Exception as e:
        err = (
            f"The Starlight daily scrape failed.\n\n"
            f"{type(e).__name__}: {e}\n\n"
            f"Traceback:\n{traceback.format_exc()}"
        )
        log(err)
        send_email(
            subject=f"[FAILED] {subject}",
            body_text=err,
            body_html=f"<pre>{err}</pre>",
            gmail_user=gmail_user,
            gmail_app_pw=gmail_app_pw,
            recipients=recipients,
        )
        return 1

    send_email(
        subject=subject,
        body_text=text,
        body_html=html,
        gmail_user=gmail_user,
        gmail_app_pw=gmail_app_pw,
        recipients=recipients,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
