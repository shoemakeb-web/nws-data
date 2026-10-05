"""Validated USDA CSV retrieval with last-good preservation and fetch metadata.

Metadata proves a successful source download, not the USDA publication time.
"""
import argparse
import csv
import hashlib
import json
import os
import tempfile
import time
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE_URL = "https://publicdashboards.dl.usda.gov/t/MRP_PUB/views/NewWorldScrewwormPublicReporting_17805168329840/ExportToCSV.csv"
OUTPUT_FILE = Path("usda_nws_cases_full.csv")
STATUS_FILE = Path("usda_nws_cases_status.json")
REQUIRED = {"Animal ID", "Confirmed Date", "County", "State", "Status"}


def normalize_header(value):
    return " ".join(str(value).strip().split())


def download_csv_text(url, timeout):
    request = Request(url, headers={'User-Agent': 'PPARTT-USDA-CSV-Updater/1.0'})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8-sig')


def parse_date(value):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("Invalid confirmed date: " + str(value))


def validate_csv(text, previous_text=None):
    if text.lstrip().startswith("<"):
        raise ValueError("Source returned HTML, not CSV")
    rows = list(csv.reader(StringIO(text.lstrip("\ufeff"))))
    if not rows:
        raise ValueError("CSV returned no header")
    headers = [normalize_header(h) for h in rows[0]]
    if len(set(headers)) != len(headers) or not REQUIRED.issubset(headers):
        raise ValueError("Missing or duplicate required CSV headers")
    records, ids = [], set()
    for row in rows[1:]:
        if not any(str(v).strip() for v in row):
            continue
        if len(row) != len(headers):
            raise ValueError("CSV row length does not match header")
        record = dict(zip(headers, row))
        if any(not str(record[k]).strip() for k in REQUIRED):
            raise ValueError("Required CSV value is blank")
        parse_date(record["Confirmed Date"])
        if record["Status"].strip().lower() not in {"active", "inactive"}:
            raise ValueError("Unrecognized case status")
        identity = record["Animal ID"].strip()
        if identity in ids:
            raise ValueError("Duplicate Animal ID")
        ids.add(identity)
        records.append(record)
    if not records:
        raise ValueError("Source returned zero case records; manual review required")
    if previous_text:
        # The full-case archive must not silently lose existing records.
        prior_reader = csv.DictReader(StringIO(previous_text.lstrip("\ufeff")))
        prior_ids = {r["Animal ID"].strip() for r in prior_reader if r.get("Animal ID")}
        if not prior_ids.issubset(ids):
            raise ValueError("Source omitted existing case IDs; last good CSV retained")
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    payload = output.getvalue().encode("utf-8")
    return payload, len(records), max(parse_date(r["Confirmed Date"]) for r in records).isoformat()


def atomic_write(path, payload):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_status(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def recovery_needed(status, now):
    if status.get("ok") is not True:
        return True
    try:
        last = datetime.fromisoformat(status["last_successful_fetch_utc"].replace("Z", "+00:00"))
        eastern = now.astimezone(ZoneInfo("America/New_York"))
        source_day = eastern.date() if eastern.hour >= 17 else eastern.date() - timedelta(days=1)
        cutoff = datetime.combine(source_day, datetime.min.time(), ZoneInfo("America/New_York")).replace(hour=17)
        return last < cutoff or last > now
    except (KeyError, ValueError, TypeError):
        return True


def update(output=OUTPUT_FILE, status_path=STATUS_FILE, now=None, get=None, sleep=time.sleep):
    output, status_path = Path(output), Path(status_path)
    now = now or datetime.now(timezone.utc)
    timestamp = now.isoformat().replace("+00:00", "Z")
    metadata = load_status(status_path)
    previous = output.read_text(encoding="utf-8") if output.exists() else None
    getter = get or download_csv_text
    last_error = None
    for attempt in range(3):
        try:
            text = getter(SOURCE_URL, timeout=60)
            payload, count, latest = validate_csv(text, previous)
            atomic_write(output, payload)
            metadata = {
                "schema_version": 1, "ok": True, "source_url": SOURCE_URL,
                "last_attempt_utc": timestamp, "last_successful_fetch_utc": timestamp,
                "row_count": count, "latest_case_date": latest,
                "csv_sha256": hashlib.sha256(payload).hexdigest(),
                "source_publication_time_verified": False, "error": None,
            }
            atomic_write(status_path, (json.dumps(metadata, indent=2) + "\n").encode("utf-8"))
            print(f"Validated {count} cases; latest case {latest}; successful download {timestamp}")
            return metadata
        except (ValueError, OSError) as error:
            last_error = error
            if attempt < 2:
                sleep(2 ** attempt)
    metadata.update({"schema_version": 1, "ok": False, "source_url": SOURCE_URL,
                     "last_attempt_utc": timestamp, "error": str(last_error)[:1000],
                     "source_publication_time_verified": False})
    atomic_write(status_path, (json.dumps(metadata, indent=2) + "\n").encode("utf-8"))
    raise RuntimeError("USDA update failed; see status file: " + str(last_error))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-only", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.recovery_only and not recovery_needed(load_status(STATUS_FILE), now):
        print("Recovery check: an evening source download already succeeded; no retry needed.")
        return
    update(now=now)


if __name__ == "__main__":
    main()
