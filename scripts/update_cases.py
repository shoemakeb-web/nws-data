import csv
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

SOURCE_URL = "https://www.aphis.usda.gov/animals/animal-health/livestock-and-poultry-disease/current-status/us-confirmed-cases-new-world"
OUTPUT_FILE = "usda_nws_cases_full.csv"

HEADERS = [
    "County",
    "State",
    "Confirmed Date",
    "Min. Confirmed Date",
    "Animal ID",
    "Animal Type",
    "Case Type",
    "Species",
    "Status"
]

def normalize_text(s):
    if s is None:
        return ""
    return " ".join(str(s).split()).strip()

def fetch_page():
    resp = requests.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()
    return resp.text

def parse_cases_from_page(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    # Try all tables on the page
    tables = soup.find_all("table")
    for table in tables:
        # Read headers
        header_cells = table.find_all("th")
        header_texts = [normalize_text(th.get_text(" ", strip=True)) for th in header_cells]

        # Look for a table that appears to be case data
        joined_headers = " | ".join(header_texts).lower()
        if not any(key in joined_headers for key in ["county", "confirmed", "date"]):
            continue

        trs = table.find_all("tr")
        if not trs:
            continue

        # Build index map from headers
        first_row = trs[0]
        ths = first_row.find_all(["th", "td"])
        headers = [normalize_text(th.get_text(" ", strip=True)) for th in ths]
        header_map = {h.lower(): i for i, h in enumerate(headers)}

        county_idx = None
        state_idx = None
        date_idx = None

        for i, h in enumerate(headers):
            hl = h.lower()
            if county_idx is None and "county" in hl:
                county_idx = i
            if state_idx is None and ("state" in hl or "st" == hl):
                state_idx = i
            if date_idx is None and ("date" in hl):
                date_idx = i

        # If we can’t find a likely case table, skip it
        if county_idx is None or date_idx is None:
            continue

        # Parse data rows
        for tr in trs[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue

            vals = [normalize_text(c.get_text(" ", strip=True)) for c in cells]
            if len(vals) <= max(county_idx, date_idx):
                continue

            county = vals[county_idx]
            state = vals[state_idx] if state_idx is not None and state_idx < len(vals) else "TX"
            confirmed_date = vals[date_idx]

            if not county or not confirmed_date:
                continue

            rows_out.append({
                "County": county,
                "State": state,
                "Confirmed Date": confirmed_date,
                "Min. Confirmed Date": confirmed_date,
                "Animal ID": "",
                "Animal Type": "",
                "Case Type": "Confirmed",
                "Species": "NWS",
                "Status": "Active"
            })

    return rows_out

def write_csv(rows):
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

def main():
    html = fetch_page()
    rows = parse_cases_from_page(html)

    # Optional fallback if no rows were found
    if not rows:
        print("No rows parsed from USDA page. Check page structure.")
    else:
        print(f"Parsed {len(rows)} rows.")

    write_csv(rows)

if __name__ == "__main__":
    main()
