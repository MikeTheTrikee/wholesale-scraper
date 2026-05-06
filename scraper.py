"""
Manatee County Probate Lead Scraper - Fixed Version
-----------------------------------------------------
Scrapes probate cases from Manatee County Clerk records,
enriches with property data from Manatee Property Appraiser,
and pushes leads to Supabase CRM.

Usage:
  python scraper.py --start 2026-04-01 --end 2026-05-05   (initial bulk load)
  python scraper.py                                         (daily: yesterday to today)
"""

import requests
from bs4 import BeautifulSoup
import time
import re
import argparse
from datetime import datetime, timedelta
from urllib.parse import quote

# ── CONFIG ────────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://siglipinabgwgwujvatm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNpZ2xpcGluYWJnd2d3dWp2YXRtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc5OTc5ODksImV4cCI6MjA5MzU3Mzk4OX0._J1P82_oLycZU--Fv7pHPQU4AfC9__FMG9aBukDS6vs"

BASE_URL = "https://records.manateeclerk.com"
DELAY = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://records.manateeclerk.com/",
}

# ── SUPABASE ──────────────────────────────────────────────────────────────────
def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates"
    }

def lead_exists(case_number):
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/leads?case_number=eq.{quote(case_number)}&select=id",
        headers=sb_headers()
    )
    try:
        return len(resp.json()) > 0
    except:
        return False

def insert_lead(lead):
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers=sb_headers(),
        json=lead
    )
    if resp.status_code in (200, 201):
        print(f"  ✅ Inserted: {lead.get('address', 'Unknown')} — {lead.get('owner', '')}")
        return True
    else:
        print(f"  ❌ Failed {resp.status_code}: {resp.text[:200]}")
        return False

# ── SEARCH PAGE ───────────────────────────────────────────────────────────────
def fetch_search_page(start_date, end_date, page=1):
    url = (
        f"{BASE_URL}/CourtRecords/Search/CaseType/{page}/25"
        f"/{start_date}/{end_date}?caseTypeId=17&filingTypeId=0"
    )
    print(f"  Fetching page {page}: {url}")
    try:
        time.sleep(DELAY)
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  ⚠️  Page {page} error: {e}")
        return None

def parse_search_page(html):
    soup = BeautifulSoup(html, "html.parser")
    cases = []

    total = 0
    for text in soup.stripped_strings:
        m = re.search(r"Matching Results:\s*(\d+)", text)
        if m:
            total = int(m.group(1))
            break

    rows = soup.select("table tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        link_tag = row.find("a", href=True)
        if not link_tag:
            continue

        href = link_tag.get("href", "")
        if "/CourtRecords/Case/" not in href:
            continue

        # Extract case number directly from URL
        case_number = href.split("/CourtRecords/Case/")[-1].strip().split("?")[0].strip("/")
        if not case_number:
            continue

        party_name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        case_status = cells[5].get_text(strip=True) if len(cells) > 5 else ""
        file_date = cells[6].get_text(strip=True) if len(cells) > 6 else ""

        cases.append({
            "case_number": case_number,
            "case_url": BASE_URL + href,
            "decedent_name": party_name,
            "case_status": case_status,
            "file_date": file_date,
        })

    return cases, total

# ── CASE DETAIL ───────────────────────────────────────────────────────────────
def fetch_case_detail(case_number, case_url=None):
    url = case_url or f"{BASE_URL}/CourtRecords/Case/{case_number}"
    print(f"    → {url}")
    try:
        time.sleep(DELAY)
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return parse_case_detail(resp.text, case_number)
    except Exception as e:
        print(f"    ⚠️  Error: {e}")
        return {}

def parse_case_detail(html, case_number):
    soup = BeautifulSoup(html, "html.parser")

    detail = {
        "case_number": case_number,
        "decedent_name": "",
        "decedent_physical": "",
        "decedent_mailing": "",
        "petitioner_name": "",
        "petitioner_physical": "",
        "petitioner_mailing": "",
        "file_date": "",
    }

    # Find parties table
    parties_table = None
    for table in soup.find_all("table"):
        txt = table.get_text()
        if "Decedent" in txt or "Petitioner" in txt:
            parties_table = table
            break

    if not parties_table:
        print(f"    ⚠️  No parties table for {case_number}")
        return detail

    current_type = ""
    for row in parties_table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        pt = cells[0].get_text(strip=True)
        if pt:
            current_type = pt

        name_cell = cells[1]
        lines = [l.strip() for l in name_cell.get_text("\n", strip=True).split("\n") if l.strip()]

        if not lines:
            continue

        # First line is the name
        name = lines[0]

        # Parse addresses
        mailing = ""
        physical = ""
        for j, line in enumerate(lines):
            if "Mailing Address:" in line:
                addr = line.replace("Mailing Address:", "").strip()
                mailing = addr if addr else (lines[j+1] if j+1 < len(lines) else "")
            elif "Physical Address:" in line:
                addr = line.replace("Physical Address:", "").strip()
                physical = addr if addr else (lines[j+1] if j+1 < len(lines) else "")

        if "Decedent" in current_type and not detail["decedent_name"]:
            detail["decedent_name"] = name
            detail["decedent_physical"] = physical
            detail["decedent_mailing"] = mailing

        elif "Petitioner" in current_type and not detail["petitioner_name"]:
            detail["petitioner_name"] = name
            detail["petitioner_physical"] = physical
            detail["petitioner_mailing"] = mailing

    return detail

# ── PROPERTY APPRAISER ────────────────────────────────────────────────────────
def get_property_data(address):
    if not address or len(address) < 5:
        return {}

    street = re.sub(
        r',?\s*(PALMETTO|BRADENTON|SARASOTA|PARRISH|ELLENTON|ANNA MARIA|HOLMES BEACH|LONGBOAT KEY|TERRA CEIA|MYAKKA CITY|ONECO|CORTEZ|MEMPHIS|TALLEVAST).*$',
        '', address, flags=re.I
    ).strip()
    street = re.sub(r'\s+FL\s+\d{5}.*$', '', street, flags=re.I).strip().strip(",")

    if not street:
        return {}

    print(f"    🏠 PAO lookup: {street}")
    try:
        resp = requests.get(
            f"https://www.manateepao.gov/search/?s={quote(street)}",
            headers={**HEADERS, "Referer": "https://www.manateepao.gov/"},
            timeout=12
        )
        if resp.status_code != 200:
            return {}

        text = resp.text
        prop = {}

        m = re.search(r'(?:Year Built|Yr Built)[^\d]*(\d{4})', text, re.I)
        if m and 1800 < int(m.group(1)) < 2030:
            prop["year_built"] = m.group(1)

        m = re.search(r'(?:Living Area|Sq\.?\s*Ft\.?|Heated Area)[^\d]*([\d,]+)', text, re.I)
        if m:
            prop["sqft"] = m.group(1).replace(",", "")

        m = re.search(r'(?:Just Value|Market Value|Assessed Value)[^\$\d]*\$?([\d,]+)', text, re.I)
        if m:
            prop["assessed_value"] = "$" + m.group(1)

        return prop
    except Exception as e:
        print(f"    ⚠️  PAO error: {e}")
        return {}

# ── MAIN ──────────────────────────────────────────────────────────────────────
def run_scraper(start_date, end_date):
    print(f"\n{'='*60}")
    print(f"Manatee Probate Scraper v2")
    print(f"Range: {start_date} → {end_date}")
    print(f"{'='*60}\n")

    html = fetch_search_page(start_date, end_date, page=1)
    if not html:
        print("Could not fetch first page. Exiting.")
        return

    cases, total = parse_search_page(html)
    pages = max(1, (total + 24) // 25)
    print(f"Total: {total} cases, {pages} pages\n")

    all_cases = list(cases)
    for page in range(2, pages + 1):
        html = fetch_search_page(start_date, end_date, page=page)
        if html:
            pc, _ = parse_search_page(html)
            all_cases.extend(pc)
            print(f"  Page {page}: +{len(pc)}")

    print(f"\nTotal collected: {len(all_cases)}\n")

    inserted = skipped = errors = 0

    for i, case in enumerate(all_cases):
        cn = case["case_number"]
        print(f"\n[{i+1}/{len(all_cases)}] {cn} — {case.get('decedent_name','')}")

        if lead_exists(cn):
            print(f"  ⏭️  Skipping duplicate")
            skipped += 1
            continue

        detail = fetch_case_detail(cn, case.get("case_url"))
        if not detail:
            errors += 1
            continue

        address = detail.get("decedent_physical") or detail.get("decedent_mailing") or ""
        owner = detail.get("decedent_name") or case.get("decedent_name", "")
        petitioner = detail.get("petitioner_name", "")
        petitioner_addr = detail.get("petitioner_physical") or detail.get("petitioner_mailing") or ""
        file_date = case.get("file_date", "")

        prop = {}
        if address:
            time.sleep(1)
            prop = get_property_data(address)

        notes = []
        if prop.get("year_built"):
            notes.append(f"Built: {prop['year_built']}")
        if prop.get("sqft"):
            notes.append(f"Sqft: {prop['sqft']}")
        if prop.get("assessed_value"):
            notes.append(f"Assessed: {prop['assessed_value']}")
        if petitioner:
            notes.append(f"Contact: {petitioner}")
        if petitioner_addr:
            notes.append(f"Contact addr: {petitioner_addr}")
        notes.append(f"Case: {cn}")

        lead = {
            "case_number": cn,
            "address": address if address else f"See case {cn}",
            "county": "Manatee",
            "type": "Probate",
            "owner": owner,
            "phone": "",
            "mail": petitioner_addr,
            "filed": file_date,
            "status": "New",
            "assigned": "Mike",
            "followup": "",
            "arv": "",
            "repair": "",
            "offermade": "No",
            "offeramt": "",
            "notes": " | ".join(notes),
        }

        if insert_lead(lead):
            inserted += 1
        else:
            errors += 1

    print(f"\n{'='*60}")
    print(f"COMPLETE — Inserted: {inserted} | Skipped: {skipped} | Errors: {errors}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    today = datetime.now()
    yesterday = today - timedelta(days=1)

    start = args.start or yesterday.strftime("%Y-%m-%d")
    end = args.end or today.strftime("%Y-%m-%d")

    run_scraper(start, end)
