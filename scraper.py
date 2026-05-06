"""
Manatee County Probate Lead Scraper v4
----------------------------------------
FIXED: Site uses POST form with internal caseId, not GET URL.
Each row has a hidden input caseId (e.g. 2640903) and a CSRF token.
We POST to /CourtRecords/Case/Details with those values.
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
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/leads?case_number=eq.{quote(case_number)}&select=id",
            headers=sb_headers()
        )
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
        print(f"  ✅ {lead.get('address','?')} — {lead.get('owner','')}")
        return True
    else:
        print(f"  ❌ {resp.status_code}: {resp.text[:150]}")
        return False

# ── SESSION ───────────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)

# ── SEARCH PAGE ───────────────────────────────────────────────────────────────
def fetch_search_page(start_date, end_date, page=1):
    url = (
        f"{BASE_URL}/CourtRecords/Search/CaseType/{page}/25"
        f"/{start_date}/{end_date}?caseTypeId=17&filingTypeId=0"
    )
    print(f"  Fetching page {page}...")
    try:
        time.sleep(DELAY)
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text, url
    except Exception as e:
        print(f"  ⚠️  Error: {e}")
        return None, url

def parse_search_page(html):
    """
    Parse data-row rows. Each row has:
    - td[0]: row number
    - td[1]: form with hidden caseId input + CSRF token
    - td[2]: case number (plain text)
    - td[3]: party name
    - td[4]: party type
    - td[5]: case type
    - td[6]: case status
    - td[7]: file date
    - td[8]: DOB
    """
    soup = BeautifulSoup(html, "html.parser")
    cases = []

    total = 0
    m = re.search(r"Matching Results:\s*(\d+)", html)
    if m:
        total = int(m.group(1))

    rows = soup.find_all("tr", class_="data-row")
    print(f"  Found {len(rows)} data-row entries")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue

        # Extract caseId and CSRF token from the form in td[1]
        form = cells[1].find("form")
        if not form:
            continue

        csrf_input = form.find("input", {"name": "__RequestVerificationToken"})
        caseid_input = form.find("input", {"name": "caseId"})
        search_addr_input = form.find("input", {"name": "searchAddress"})

        if not caseid_input:
            continue

        case_id = caseid_input.get("value", "")
        csrf_token = csrf_input.get("value", "") if csrf_input else ""
        search_addr = search_addr_input.get("value", "") if search_addr_input else ""

        # Case number from td[2]
        case_number = cells[2].get_text(strip=True)
        party_name = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        party_type = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        case_status = cells[6].get_text(strip=True) if len(cells) > 6 else ""
        file_date = cells[7].get_text(strip=True) if len(cells) > 7 else ""

        if not case_number or not case_id:
            continue

        cases.append({
            "case_number": case_number,
            "case_id": case_id,
            "csrf_token": csrf_token,
            "search_addr": search_addr,
            "decedent_name": party_name,
            "party_type": party_type,
            "case_status": case_status,
            "file_date": file_date,
        })

    return cases, total

# ── CASE DETAIL ───────────────────────────────────────────────────────────────
def fetch_case_detail(case_info, search_page_url):
    """POST to case details endpoint using caseId and CSRF token."""
    post_url = f"{BASE_URL}/CourtRecords/Case/Details"
    case_number = case_info["case_number"]
    
    post_data = {
        "__RequestVerificationToken": case_info["csrf_token"],
        "caseId": case_info["case_id"],
        "searchAddress": case_info.get("search_addr", search_page_url),
    }

    print(f"    → POST case {case_number} (id={case_info['case_id']})")
    try:
        time.sleep(DELAY)
        resp = session.post(
            post_url,
            data=post_data,
            headers={
                "Referer": search_page_url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=20
        )
        resp.raise_for_status()
        return parse_case_detail(resp.text, case_number)
    except Exception as e:
        print(f"    ⚠️  POST error: {e}")
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
        if not cells:
            continue

        pt = cells[0].get_text(strip=True)
        if pt and len(pt) < 60:
            current_type = pt

        if len(cells) < 2:
            continue

        name_cell = cells[1]
        lines = [l.strip() for l in name_cell.get_text("\n", strip=True).split("\n") if l.strip()]

        if not lines:
            continue

        name = lines[0]
        mailing = ""
        physical = ""

        for j, line in enumerate(lines):
            if "Mailing Address:" in line:
                val = line.replace("Mailing Address:", "").strip()
                mailing = val if val else (lines[j+1] if j+1 < len(lines) else "")
            elif "Physical Address:" in line:
                val = line.replace("Physical Address:", "").strip()
                physical = val if val else (lines[j+1] if j+1 < len(lines) else "")

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
    ).strip().strip(",").strip()
    street = re.sub(r'\s+FL\s+\d{5}.*$', '', street, flags=re.I).strip()

    if not street or len(street) < 4:
        return {}

    print(f"    🏠 PAO: {street}")
    try:
        resp = session.get(
            f"https://www.manateepao.gov/search/?s={quote(street)}",
            headers={"Referer": "https://www.manateepao.gov/"},
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
    print(f"Manatee Probate Scraper v4")
    print(f"Range: {start_date} → {end_date}")
    print(f"{'='*60}\n")

    html, page1_url = fetch_search_page(start_date, end_date, page=1)
    if not html:
        print("Could not fetch page 1.")
        return

    cases, total = parse_search_page(html)
    pages = max(1, (total + 24) // 25)
    print(f"Total: {total} cases, {pages} pages. Page 1: {len(cases)} parsed.\n")

    all_cases = list(cases)
    page_urls = {1: page1_url}

    for page in range(2, pages + 1):
        html, purl = fetch_search_page(start_date, end_date, page=page)
        if html:
            pc, _ = parse_search_page(html)
            all_cases.extend(pc)
            page_urls[page] = purl
            print(f"  Page {page}: +{len(pc)}")

    print(f"\nTotal collected: {len(all_cases)}\nProcessing...\n")

    inserted = skipped = errors = 0

    for i, case in enumerate(all_cases):
        cn = case["case_number"]
        print(f"\n[{i+1}/{len(all_cases)}] {cn} — {case.get('decedent_name','')}")

        if lead_exists(cn):
            print(f"  ⏭️  Already exists")
            skipped += 1
            continue

        detail = fetch_case_detail(case, page1_url)
        if not detail:
            errors += 1
            continue

        address = detail.get("decedent_physical") or detail.get("decedent_mailing") or ""
        owner = detail.get("decedent_name") or case.get("decedent_name", "")
        petitioner = detail.get("petitioner_name", "")
        petitioner_addr = detail.get("petitioner_physical") or detail.get("petitioner_mailing") or ""

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
            "filed": case.get("file_date", ""),
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
    print(f"DONE — Inserted: {inserted} | Skipped: {skipped} | Errors: {errors}")
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
