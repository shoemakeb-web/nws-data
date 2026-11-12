# nws-data

The updater downloads the USDA New World Screwworm CSV and preserves the last
good archive if a download or validation check fails. Run its tests with:

```sh
python -m unittest discover -s tests -v
```

## Verified USDA identity correction

The USDA export changed one composite Animal ID from
`Texas, BREWSTER, SU:28426:27, SS:12802:27` to
`Texas, JEFF DAVIS, SU:28426:27, SS:12802:27`. Comparing the retained November 3
snapshot with a fresh November 12, 2026 download confirmed that only Animal ID
and County changed on this record. The SU/SS identifiers, state, species,
animal/case types, and confirmed date were unchanged.

`scripts/update_cases.py` permits this exact, one-way correction only when the
old ID is missing, the new ID is present, both county values match, and both
records have the expected Texas / Domestic / Domestic / Cattle context. It
does not discard county information from other IDs or match records by date.
The source's corrected ID is retained without adding a duplicate historical row.

Both IDs appearing together, duplicate prior IDs, missing identity context,
unexpected replacements, reversals, and every other unexplained omission still
fail validation. Status and confirmation-date updates remain allowed. Further
identity corrections need separate source verification and regression tests.

Source: https://publicdashboards.dl.usda.gov/t/MRP_PUB/views/NewWorldScrewwormPublicReporting_17805168329840/ExportToCSV.csv

Run `python scripts/update_cases.py` to download, validate, and atomically write
the CSV and fetch-status files. The successful-fetch timestamp proves a source
download, not the time USDA published the data.
