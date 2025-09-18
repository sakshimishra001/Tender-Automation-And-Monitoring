#!/usr/bin/env python3
"""



Install:
    pip install requests beautifulsoup4 python-dotenv

Run:
    # Dry run (no email)
    DRY_RUN=1 python tender_notifier.py

    # Real run (set SMTP config env vars )
    python tender_notifier.py
"""

import os
import re
import json
import logging
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from email.message import EmailMessage
import smtplib
from dotenv import load_dotenv
load_dotenv() 

# -------------- CONFIG --------------
BASE_URL = os.environ.get("BASE_URL", "https://iimranchi.ac.in/tender/")
SEEN_FILE = os.environ.get("SEEN_FILE", "seen_tenders.json")
KEYWORDS = os.environ.get(
    "KEYWORDS",
    "eprocurment,etender,eauction,esale,edisposal,government ERP,enilami,ebidding,contract management,measurement book,ebilling"
)
KEYWORDS = [k.strip().lower() for k in KEYWORDS.split(",") if k.strip()]

SMTP_HOST = os.environ.get("SMTP_HOST", None)       # e.g. smtp.gmail.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", None)
SMTP_PASS = os.environ.get("SMTP_PASS", None)
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)
TO_EMAILS = [e.strip() for e in os.environ.get("TO_EMAILS", "").split(",") if e.strip()]

DRY_RUN = os.environ.get("DRY_RUN", "") not in ("", "0", "false", "False")

REQUEST_TIMEOUT = 15
HEADERS = {"User-Agent": "TenderNotifier/1.0 (+https://yourorg.example)"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# -------------- helpers --------------
def fetch_page(url):
    logging.info("Fetching %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


DATE_RE = re.compile(r"\d{1,2}\s+\w+\s+\d{4}")  # e.g. "4 Sep 2025"


def parse_tenders(html, base_url):
    """
    Returns list of {title, href, date, snippet}
    """
    soup = BeautifulSoup(html, "html.parser")

    # Try to narrow to main content if present
    content = soup.find("main") or soup.find("div", id="content") or soup

    items = {}
    for a in content.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 6:
            continue

        href = urljoin(base_url, a["href"])
        parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
        # find date near the anchor text (fallback: global search)
        date_match = DATE_RE.search(parent_text) or DATE_RE.search(content.get_text(" ", strip=True))
        date = date_match.group(0) if date_match else None

        # snippet: parent_text minus title (trimmed)
        snippet = parent_text.replace(title, "").strip()[:300]

        # deduplicate by href
        if href in items:
            continue
        items[href] = {"title": title, "href": href, "date": date, "snippet": snippet}
    return list(items.values())


def is_relevant(title, keywords):
    s = title.lower()
    for k in keywords:
        if k and k.lower() in s:
            return True
    return False


def load_seen(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen(path, seen):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


def send_email_smtp(subject, body, to_addrs):
    if DRY_RUN:
        logging.info("[dry-run] would send email to %s: %s", to_addrs, subject)
        return True

    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, FROM_EMAIL, to_addrs]):
        logging.error("SMTP not configured properly; cannot send email.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)
    # simple HTML alternative
    html_body = body.replace("\n", "<br>")
    msg.add_alternative(f"<html><body>{html_body}</body></html>", subtype="html")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        logging.info("Email sent to %s", to_addrs)
        return True
    except Exception as e:
        logging.exception("Failed to send email: %s", e)
        return False


# -------------- main logic --------------
def main():
    html = fetch_page(BASE_URL)
    tenders = parse_tenders(html, BASE_URL)
    logging.info("Parsed %d anchors; %d candidate tenders", len(tenders), len(tenders))

    # filter relevant
    relevant = [t for t in tenders if is_relevant(t["title"], KEYWORDS)]
    logging.info("Found %d relevant tenders after keyword filter", len(relevant))

    seen = load_seen(SEEN_FILE)  # map href -> metadata
    new_items = []
    for t in relevant:
        if t["href"] not in seen:
            new_items.append(t)

    logging.info("Detected %d NEW relevant tenders", len(new_items))

    # notify and mark seen
    for t in new_items:
        subj = f"NEW Tender: {t['title']}"
        body = (
            f"Title: {t['title']}\n"
            f"Date: {t.get('date')}\n"
            f"Link: {t['href']}\n\n"
            f"{t.get('snippet')}\n\n"
            "----\n"
            f"Detected: {datetime.utcnow().isoformat()} UTC\n"
        )
        ok = send_email_smtp(subj, body, TO_EMAILS or [SMTP_USER] if SMTP_USER else [])
        # if SMTP not configured and DRY_RUN False, send_email_smtp returns False; still mark as seen only on success? We'll mark anyway but flag.
        seen[t["href"]] = {
            "title": t["title"],
            "date": t.get("date"),
            "notified": ok,
            "first_seen_utc": datetime.utcnow().isoformat(),
        }

    save_seen(SEEN_FILE, seen)
    logging.info("Updated seen file: %s", SEEN_FILE)


if __name__ == "__main__":
    main()

