"""
Manatee County Probate Lead Scraper
------------------------------------
Scrapes probate cases from Manatee County Clerk records,
enriches with property data from Manatee Property Appraiser,
and pushes leads to Supabase CRM.

Usage:
  python scraper.py --start 2026-04-01 --end 2026-05-05   (initial bulk load)
  python scraper.py                                         (daily: yesterday to today)

Deploy on Railway.app with a daily cron schedule.
"""

import requests
from bs4 import BeautifulSoup
import time
import json
import argparse
import re
from datetime import datetime, timedelta
from urllib.parse import quote

# ── CONFIG ────────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://siglipinabgwgwujvatm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNpZ2xpcGluYWJnd2d3dWp2YXRtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc5OTc5ODksImV4cCI6MjA5MzU3Mzk4OX0._J1P82_oLycZU--Fv7pHPQU4AfC9__FMG9aBukDS6vs"

BASE_URL = "https://records.manateeclerk.com"
APPRAISER_URL = "https://www.manateepao.gov/api/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://records.manateeclerk.com/",
}

DELAY = 2.5  # seconds between requests — respectful scraping

# ── SUPABASE ──────────────────────────────────────────────────────────────────
def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates"
    }

def lead_exists(case_number):
    """Check if case already exists in DB to avoid duplicates."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/leads?case_number=eq.{case_number}&select=id",
        headers=supabase_headers()
    )
    return len(resp.json()) > 0

def insert_lead(lead):
    """Insert a lead into Supabase."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers=supabase_headers(),
        json=lead
    )
    if resp.status_code in (200, 201):
        print(f"  ✅ Inserted: {lead.get('address', 'Unknown address')}")
        return True
    else:
        print(f"  ❌ Insert failed: {resp.status_code} {resp.text[:200]}")
        return False

# ── PROBATE SEARCH ────────────────────────────────────────────────────────────
def get_probate_cases(start_date, end_date, page=1):
    """Fetch one page of probate search results."""
    url = (
        f"{BASE_URL}/CourtRecords/Search/CaseType/{page}/25"
        f"/{start_date}/{end_date}?caseTypeId=17&filingTypeId=0"
    )
    print(f"  Fetching search page {page}: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  ⚠️  Error fetching page {page}: {e}")
        return None

def parse_case_list(html):
    """Parse case numbers and basic info from search results page."""
    soup = BeautifulSoup(html, "html.parser")
    cases = []

    # Get total results count
    total_text = soup.find(string=re.compile(r"Matching Results:"))
    total = 0
    if total_text:
        match = re.search(r"(\d+)", total_text)
        if match:
            total = int(match.group(1))

    # Find table rows
    table = soup.find("table")
    if not table:
        return cases, total

    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        # Get case number and link
        case_link = row.find("a", href=re.compile(r"/CourtRecords/Case/"))
        if not case_link:
            # Try finding case number in cell text
            case_cell = cells[1] if len(cells) > 1 else None
            if not case_cell:
                continue
            case_number = case_cell.get_text(strip=True)
            case_url = None
        else:
            case_number = case_link.get_text(strip=True)
            case_url = BASE_URL + case_link["href"]

        # Get party name from cell
        party_name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        party_type = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        case_status = cells[5].get_text(strip=True) if len(cells) > 5 else ""
        file_date = cells[6].get_text(strip=True) if len(cells) > 6 else ""

        if case_number and "CP" in case_number:  # Probate case numbers contain CP
            cases.append({
                "case_number": case_number,
                "case_url": case_url,
                "decedent_name": party_name if "Decedent" in party_type else "",
                "case_status": case_status,
                "file_date": file_date,
            })

    return cases, total

# ── CASE DETAIL ───────────────────────────────────────────────────────────────
def get_case_detail(case_number):
    """Fetch and parse individual case detail page."""
    url = f"{BASE_URL}/CourtRecords/Case/{case_number}"
    print(f"    Fetching case detail: {case_number}")
    try:
        time.sleep(DELAY)
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return parse_case_detail(resp.text, case_number)
    except Exception as e:
        print(f"    ⚠️  Error fetching case {case_number}: {e}")
        return {}

def parse_case_detail(html, case_number):
    """Extract parties and addresses from case detail page."""
    soup = BeautifulSoup(html, "html.parser")
    detail = {
        "case_number": case_number,
        "decedent_name": "",
        "decedent_address": "",
        "petitioner_name": "",
        "petitioner_address": "",
    }

    # Find parties section
    parties_section = soup.find(string=re.compile(r"Parties", re.I))
    if not parties_section:
        return detail

    # Find the parties table
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            party_type = cells[0].get_text(strip=True)
            # Name and address are usually in the second cell with line breaks
            name_cell = cells[1]
            cell_text = name_cell.get_text("\n", strip=True)
            lines = [l.strip() for l in cell_text.split("\n") if l.strip()]

            if not lines:
                continue

            name = lines[0]

            # Extract addresses from cell
            mailing_addr = ""
            physical_addr = ""
            for line in lines:
                if "Mailing Address:" in line:
                    mailing_addr = line.replace("Mailing Address:", "").strip()
                elif "Physical Address:" in line:
                    physical_addr = line.replace("Physical Address:", "").strip()

            address = physical_addr or mailing_addr

            if "Decedent" in party_type:
                detail["decedent_name"] = name
                detail["decedent_address"] = address
            elif "Petitioner" in party_type and not detail["petitioner_name"]:
                detail["petitioner_name"] = name
                detail["petitioner_address"] = address

    return detail

# ── PROPERTY APPRAISER ────────────────────────────────────────────────────────
def get_property_data(address):
    """Look up property data from Manatee Property Appraiser."""
    if not address:
        return {}

    # Clean address for search — remove city/state/zip
    street = re.sub(r',?\s*(PALMETTO|BRADENTON|SARASOTA|PARRISH|ELLENTON|MYAKKA CITY|ANNA MARIA|HOLMES BEACH|LONGBOAT KEY|TERRA CEIA).*$', '', address, flags=re.I).strip()
    # Remove FL and zip
    street = re.sub(r'\s+FL\s+\d{5}.*$', '', street, flags=re.I).strip()

    print(f"    Looking up property: {street}")

    try:
        # Try Manatee PAO search API
        search_url = f"https://www.manateepao.gov/search/?s={quote(street)}"
        resp = requests.get(
            search_url,
            headers={**HEADERS, "Referer": "https://www.manateepao.gov/"},
            timeout=10
        )

        if resp.status_code != 200:
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for property details in results
        prop_data = {}

        # Search for year built
        year_built_pattern = re.search(r'(?:Year Built|Built in|Year:)\s*:?\s*(\d{4})', resp.text, re.I)
        if year_built_pattern:
            prop_data["year_built"] = year_built_pattern.group(1)

        # Search for assessed value
        value_pattern = re.search(r'(?:Assessed Value|Just Value|Market Value)\s*:?\s*\$?([\d,]+)', resp.text, re.I)
        if value_pattern:
            prop_data["assessed_value"] = "$" + value_pattern.group(1)

        # Search for square footage
        sqft_pattern = re.search(r'(?:Living Area|Sq\.?\s*Ft\.?|Square Feet)\s*:?\s*([\d,]+)', resp.text, re.I)
        if sqft_pattern:
            prop_data["sqft"] = sqft_pattern.group(1).replace(",", "")

        return prop_data

    except Exception as e:
        print(f"    ⚠️  Property lookup error: {e}")
        return {}

# ── MAIN SCRAPER ──────────────────────────────────────────────────────────────
def run_scraper(start_date, end_date):
    print(f"\n{'='*60}")
    print(f"Manatee Probate Scraper")
    print(f"Date range: {start_date} to {end_date}")
    print(f"{'='*60}\n")

    # Step 1: Get first page to find total count
    print("Step 1: Fetching case list...")
    html = get_probate_cases(start_date, end_date, page=1)
    if not html:
        print("Failed to fetch first page. Exiting.")
        return

    cases, total = parse_case_list(html)
    pages = (total // 25) + (1 if total % 25 else 0)
    print(f"Found {total} total cases across {pages} pages\n")

    all_cases = list(cases)

    # Fetch remaining pages
    for page in range(2, pages + 1):
        time.sleep(DELAY)
        html = get_probate_cases(start_date, end_date, page=page)
        if html:
            page_cases, _ = parse_case_list(html)
            all_cases.extend(page_cases)
            print(f"  Page {page}: found {len(page_cases)} cases")

    print(f"\nTotal cases collected: {len(all_cases)}")

    # Step 2: Process each case
    print("\nStep 2: Processing case details...")
    inserted = 0
    skipped = 0
    errors = 0

    for i, case in enumerate(all_cases):
        case_number = case["case_number"]
        print(f"\n[{i+1}/{len(all_cases)}] {case_number}")

        # Skip if already in DB
        if lead_exists(case_number):
            print(f"  ⏭️  Already exists, skipping")
            skipped += 1
            continue

        # Get case detail
        detail = get_case_detail(case_number)
        if not detail:
            errors += 1
            continue

        address = detail.get("decedent_address", "")

        # Get property appraiser data
        prop_data = {}
        if address:
            time.sleep(1)
            prop_data = get_property_data(address)

        # Build notes field
        notes_parts = []
        if prop_data.get("year_built"):
            notes_parts.append(f"Built: {prop_data['year_built']}")
        if prop_data.get("sqft"):
            notes_parts.append(f"Sqft: {prop_data['sqft']}")
        if prop_data.get("assessed_value"):
            notes_parts.append(f"Assessed: {prop_data['assessed_value']}")
        if detail.get("petitioner_name"):
            notes_parts.append(f"Petitioner: {detail['petitioner_name']}")
        if detail.get("petitioner_address"):
            notes_parts.append(f"Petitioner addr: {detail['petitioner_address']}")
        notes_parts.append(f"Case: {case_number} | Filed: {case.get('file_date','')}")

        # Build lead record matching your CRM schema
        lead = {
            "address": address or f"See case {case_number}",
            "county": "Manatee",
            "type": "Probate",
            "owner": detail.get("decedent_name", ""),
            "phone": "",  # To be filled by Skipify
            "mail": detail.get("petitioner_address", ""),
            "filed": case.get("file_date", ""),
            "status": "New",
            "assigned": "Mike",
            "followup": "",
            "arv": "",
            "repair": "",
            "offermade": "No",
            "offeramt": "",
            "notes": " | ".join(notes_parts),
            # Extra fields stored in notes since they're not separate columns yet
        }

        success = insert_lead(lead)
        if success:
            inserted += 1
        else:
            errors += 1

        # Respectful delay between cases
        time.sleep(DELAY)

    print(f"\n{'='*60}")
    print(f"SCRAPE COMPLETE")
    print(f"  Inserted: {inserted}")
    print(f"  Skipped (duplicates): {skipped}")
    print(f"  Errors: {errors}")
    print(f"  Total processed: {len(all_cases)}")
    print(f"{'='*60}\n")

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manatee County Probate Lead Scraper")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: yesterday)", default=None)
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: today)", default=None)
    args = parser.parse_args()

    today = datetime.now()
    yesterday = today - timedelta(days=1)

    start = args.start or yesterday.strftime("%Y-%m-%d")
    end = args.end or today.strftime("%Y-%m-%d")

    run_scraper(start, end)
