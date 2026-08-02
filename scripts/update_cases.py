import csv
import requests
from io import StringIO

SOURCE_URL = "https://publicdashboards.dl.usda.gov/t/MRP_PUB/views/NewWorldScrewwormPublicReporting_17805168329840/ExportToCSV.csv"
OUTPUT_FILE = "usda_nws_cases_full.csv"

def normalize_header(h):
    return " ".join(str(h).strip().split())

def main():
    resp = requests.get(SOURCE_URL, timeout=60)
    resp.raise_for_status()

    text = resp.text
    print("Downloaded CSV length:", len(text))

    reader = csv.reader(StringIO(text))
    rows = list(reader)

    if not rows:
        raise RuntimeError("CSV download returned no rows.")

    headers = [normalize_header(h) for h in rows[0]]
    data_rows = rows[1:]

    print("Header count:", len(headers))
    print("Data row count:", len(data_rows))

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(data_rows)

    print(f"Wrote {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
