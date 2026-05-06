"""
BatchSkipTracing Integration
-----------------------------
Pulls leads with no phone number from Supabase,
skip traces them via BatchData API,
and updates the CRM with cell phone numbers.

Usage:
  python skiptracer.py          # traces all leads missing phone numbers
  python skiptracer.py --limit 50   # traces first 50 untraced leads
"""

import requests
import time
import argparse
import json
from urllib.parse import quote

# ── CONFIG ────────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://siglipinabgwgwujvatm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNpZ2xpcGluYWJnd2d3dWp2YXRtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc5OTc5ODksImV4cCI6MjA5MzU3Mzk4OX0._J1P82_oLycZU--Fv7pHPQU4AfC9__FMG9aBukDS6vs"

BATCH_API_KEY = "LGBQLzWXBMyp6khFDBwQdKmwhIvjNh2eVhkFedr3"
BATCH_API_URL = "https://api.batchdata.com/api/v1/property/skip-trace"

BATCH_SIZE = 100  # max per API call
DELAY = 2

# ── SUPABASE ──────────────────────────────────────────────────────────────────
def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def get_untraced_leads(limit=None):
    """Get all leads that have no phone number and have a real address."""
    url = f"{SUPABASE_URL}/rest/v1/leads?phone=eq.&address=not.ilike.See case*&select=id,address,owner"
    if limit:
        url += f"&limit={limit}"
    resp = requests.get(url, headers=sb_headers())
    try:
        leads = resp.json()
        print(f"Found {len(leads)} untraced leads")
        return leads
    except:
        print(f"Error fetching leads: {resp.text}")
        return []

def update_lead_phone(lead_id, phones, emails=""):
    """Update a lead record with phone numbers."""
    # Format multiple phones as comma separated
    phone_str = ", ".join(phones) if phones else ""
    
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
        headers=sb_headers(),
        json={"phone": phone_str}
    )
    return resp.status_code in (200, 201, 204)

# ── BATCH SKIP TRACE ──────────────────────────────────────────────────────────
def skip_trace_batch(leads_batch):
    """
    Send a batch of leads to BatchData API for skip tracing.
    Returns dict of {lead_id: [phone_numbers]}
    """
    # Build request payload
    requests_list = []
    for lead in leads_batch:
        address = lead.get("address", "")
        owner = lead.get("owner", "")
        
        if not address or "See case" in address:
            continue
            
        # Parse address components
        parts = address.split(",")
        street = parts[0].strip() if parts else address
        
        # Try to extract city/state/zip
        city = ""
        state = "FL"
        zip_code = ""
        
        if len(parts) >= 2:
            city_state = parts[1].strip()
            city_parts = city_state.split()
            if city_parts:
                # Last part might be zip
                if city_parts[-1].isdigit() and len(city_parts[-1]) == 5:
                    zip_code = city_parts[-1]
                    city = " ".join(city_parts[:-1])
                else:
                    city = city_state
        
        req = {
            "propertyAddress": {
                "street": street,
                "city": city,
                "state": state,
                "zip": zip_code,
            }
        }
        
        if owner:
            req["ownerName"] = owner
            
        requests_list.append({
            "leadId": lead["id"],
            "request": req
        })
    
    if not requests_list:
        return {}

    # Make API call
    payload = {
        "requests": [r["request"] for r in requests_list]
    }
    
    print(f"  Sending {len(requests_list)} records to BatchData...")
    
    try:
        resp = requests.post(
            BATCH_API_URL,
            headers={
                "Authorization": f"Bearer {BATCH_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60
        )
        
        if resp.status_code != 200:
            print(f"  ❌ API error {resp.status_code}: {resp.text[:300]}")
            return {}
            
        data = resp.json()
        
        # Parse results
        results = {}
        responses = data.get("results", data.get("responses", []))
        
        for i, result in enumerate(responses):
            if i >= len(requests_list):
                break
                
            lead_id = requests_list[i]["leadId"]
            phones = []
            
            # Extract phone numbers — prefer mobile
            phone_data = result.get("phoneNumbers", result.get("phones", []))
            
            for phone in phone_data:
                number = phone.get("number", phone.get("phoneNumber", ""))
                phone_type = phone.get("type", phone.get("phoneType", "")).lower()
                
                if number:
                    # Prioritize mobile numbers
                    if "mobile" in phone_type or "cell" in phone_type:
                        phones.insert(0, number)
                    elif "landline" not in phone_type and "land" not in phone_type:
                        phones.append(number)
            
            # If no mobile found, include all non-landlines
            if not phones:
                for phone in phone_data:
                    number = phone.get("number", phone.get("phoneNumber", ""))
                    if number:
                        phones.append(number)
            
            results[lead_id] = phones[:3]  # max 3 numbers per lead
            
        return results
        
    except Exception as e:
        print(f"  ⚠️  API call error: {e}")
        return {}

# ── MAIN ──────────────────────────────────────────────────────────────────────
def run_skiptracer(limit=None):
    print(f"\n{'='*60}")
    print(f"BatchSkipTracing Integration")
    print(f"{'='*60}\n")
    
    # Get untraced leads
    leads = get_untraced_leads(limit)
    if not leads:
        print("No untraced leads found. All leads may already have phone numbers.")
        return
    
    # Process in batches of 100
    total = len(leads)
    updated = 0
    no_result = 0
    errors = 0
    
    for i in range(0, total, BATCH_SIZE):
        batch = leads[i:i+BATCH_SIZE]
        print(f"\nBatch {i//BATCH_SIZE + 1}: Processing leads {i+1}-{min(i+BATCH_SIZE, total)} of {total}")
        
        results = skip_trace_batch(batch)
        
        # Update each lead with results
        for lead in batch:
            lead_id = lead["id"]
            phones = results.get(lead_id, [])
            
            if phones:
                success = update_lead_phone(lead_id, phones)
                if success:
                    print(f"  ✅ {lead.get('address','?')[:50]} → {phones[0]}")
                    updated += 1
                else:
                    print(f"  ❌ Failed to update {lead_id}")
                    errors += 1
            else:
                print(f"  ⚠️  No phone found: {lead.get('address','?')[:50]}")
                no_result += 1
        
        if i + BATCH_SIZE < total:
            print(f"  Waiting before next batch...")
            time.sleep(DELAY)
    
    print(f"\n{'='*60}")
    print(f"SKIP TRACE COMPLETE")
    print(f"  Updated with phones: {updated}")
    print(f"  No result found:     {no_result}")
    print(f"  Errors:              {errors}")
    print(f"  Total processed:     {total}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max leads to skip trace")
    args = parser.parse_args()
    run_skiptracer(args.limit)
