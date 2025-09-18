#!/usr/bin/env python3


import os
import sqlite3
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import time

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ----------------------------
# Config: Keywords (exact list you provided + a couple helpful variants)
# ----------------------------
KEYWORDS = [
    "eprocurment",   # as provided (note: spelling as user gave)
    "etender",
    "eauction",
    "esale",
    "edisposal",
    "government ERP",
    "enilami",
    "ebidding",
    "contract management",
    "measurement book",
    "ebilling",
    # helpful variants included to increase match coverage (you can remove/change)
    "eprocure",
    "eprocurement",
]

# ----------------------------
# DB settings
# ----------------------------
DB_PATH = "eprocure_tenders.db"

def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS tenders (
        tender_id TEXT PRIMARY KEY,
        title TEXT,
        organisation TEXT,
        closing_date TEXT,
        link TEXT,
        scraped_at TEXT
    )
    """)
    conn.commit()
    return conn

# ----------------------------
# Selenium driver
# ----------------------------
def get_driver(visible=True):
    """
    Returns a Chrome webdriver instance. `visible=True` keeps browser open (not headless).
    """
    options = Options()
    if visible:
        # do NOT enable headless so you can manually solve CAPTCHA
        options.add_argument("--start-maximized")
  
    else:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
    # Optional: quiet logging
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    print("🔹 Downloading/locating ChromeDriver via webdriver-manager...")
    service = Service(ChromeDriverManager().install())
    print("🔹 Starting Chrome browser...")
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# ----------------------------
# Relevance filter
# ----------------------------
def is_relevant_tender(title, organisation, extra_text=""):
    text = (title or "") + " " + (organisation or "") + " " + (extra_text or "")
    text = text.lower()
    for kw in KEYWORDS:
        if kw.lower() in text:
            return True
    return False

# ----------------------------
# Scrape function
# ----------------------------
def scrape_latest_tenders(driver, conn):
    """
    Navigates to the Latest Active Tenders page and scrapes results.
    """
    url = "https://eprocure.gov.in/eprocure/app?page=FrontEndLatestActiveTenders&service=page"
    print(f"🔹 Opening page: {url}")
    driver.get(url)

    # Wait for manual CAPTCHA resolution: interactive flow
    print("⚠️  If CAPTCHA appears in the browser, please solve it manually now.")
    print("⚠️  After solving CAPTCHA and verifying the tender list is visible, return to this terminal and press Enter to continue.")
    input("Press Enter here once the listing is visible in the browser...")

    # parse the page
    soup = BeautifulSoup(driver.page_source, "lxml")

    # Default selector used previously on eProcure listing pages
    rows = soup.select("table.list_table tr")
    if not rows or len(rows) <= 1:
        # fallback: try generic table row selector (may return too many)
        rows = soup.select("table tr")
        print("⚠️ Warning: default table selector returned no rows; using fallback selector. If results are incorrect, update the selector in script.")

    c = conn.cursor()
    new_count = 0
    processed = 0

    for row in rows:
        tds = row.find_all("td")
        if not tds:
            # header or non-data row
            continue
        processed += 1
        # Defensive column mapping — adjust indices if site uses different layout
        # Typical column layout (may vary): [tender id, title, organisation, value, closing date, ...]
        tender_id = tds[0].get_text(strip=True) if len(tds) > 0 else ""
        title = tds[1].get_text(strip=True) if len(tds) > 1 else ""
        organisation = tds[2].get_text(strip=True) if len(tds) > 2 else ""
        closing_date = tds[4].get_text(strip=True) if len(tds) > 4 else ""

        # Try to capture anchor/link if present in the row (first anchor)
        link = None
        a = row.find("a")
        if a and a.has_attr("href"):
            href = a["href"]
            # Resolve relative urls
            if href.startswith("http"):
                link = href
            else:
                link = urljoin(url, href)

        # extra_text to search inside: concatenation of all td text
        extra_text = " ".join([td.get_text(strip=True) for td in tds])
        if is_relevant_tender(title, organisation, extra_text):
            try:
                c.execute(
                    "INSERT INTO tenders (tender_id, title, organisation, closing_date, link, scraped_at) VALUES (?,?,?,?,?,?)",
                    (tender_id, title, organisation, closing_date, link or "", datetime.utcnow().isoformat())
                )
                conn.commit()
                new_count += 1
                print(f"✅ Added relevant tender: {title[:80]}  (ID: {tender_id})")
            except sqlite3.IntegrityError:
                # Already present
                print(f"ℹ️  Already in DB (skipped): {tender_id} - {title[:60]}")
            except Exception as e:
                print("❌ DB insert error:", e)
        else:
            print(f"⛔ Skipped (not matching keywords): {title[:80]}")

    print(f"🔹 Done scraping. Processed rows: {processed}. New tenders added: {new_count}.")

# ----------------------------
# Main
# ----------------------------
def main():
    print("🔹 Initializing database...")
    conn = init_db()
    driver = None
    try:
        print("🔹 Launching Chrome via Selenium (visible)...")
        driver = get_driver(visible=True)
        print("🔹 Chrome launched. Beginning scraping.")
        scrape_latest_tenders(driver, conn)
    except Exception as e:
        print("❌ ERROR during run:", e)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        conn.close()
        print("✅ Finished. SQLite DB file:", os.path.abspath(DB_PATH))

if __name__ == "__main__":
    main()
