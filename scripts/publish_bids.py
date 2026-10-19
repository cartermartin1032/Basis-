#!/usr/bin/env python3
"""Turn a fetch_bids.py CSV into data/bids_latest.json and append new rows to data/bids_history.csv.

History is deduped by date + location + commodity + delivery period. Re-runs on the
same day refresh the latest board without adding another history row.
"""
import csv
import json
import sys
import datetime
from pathlib import Path

FIELDS = ["date", "commodity", "location", "delivery_period", "cash", "futures_month", "futures", "basis"]
LOC_ORDER = ["ADM Havana", "Cargill Havana", "CHS Havana"]


def num(value):
    return round(float(value), 4)


def sort_key(row):
    crop = {"Corn": 0, "Soybeans": 1}.get(row["commodity"], 2)
    try:
        loc = LOC_ORDER.index(row["location"])
    except ValueError:
        loc = 100
    return (crop, loc, row["location"], row["delivery_period"])


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: publish_bids.py fetch.csv")
    src = Path(sys.argv[1])
    root = Path("data")
    root.mkdir(exist_ok=True)
    latest_path = root / "bids_latest.json"
    hist_path = root / "bids_history.csv"

    with src.open(newline="") as handle:
        fetched = list(csv.DictReader(handle))
    rows = []
    for raw in fetched:
        try:
            row = {
                "date": raw["date"],
                "commodity": raw["commodity"],
                "location": raw["location"],
                "delivery_period": raw["delivery_period"],
                "cash": num(raw["cash"]),
                "futures_month": raw["futures_month"],
                "futures": num(raw["futures"]),
                "basis": num(raw["basis"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            sys.exit(f"bad bid row: {exc}")
        rows.append(row)
    if not rows:
        sys.exit("no bids in fetch; leaving published data unchanged")
    rows.sort(key=sort_key)

    meta_path = src.with_suffix(".meta.json")
    notes, sources = [], {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        notes = list(meta.get("notes") or [])
        sources = dict(meta.get("sources") or {})

    fetched_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    latest_path.write_text(json.dumps({
        "fetched_at": fetched_at,
        "notes": notes,
        "sources": sources,
        "bids": rows,
    }, indent=2) + "\n")

    existing = []
    if hist_path.exists() and hist_path.stat().st_size:
        with hist_path.open(newline="") as handle:
            existing = list(csv.DictReader(handle))
    seen = {(r["date"], r["location"], r["commodity"], r["delivery_period"]) for r in existing}
    added = 0
    for row in rows:
        key = (row["date"], row["location"], row["commodity"], row["delivery_period"])
        if key in seen:
            continue
        seen.add(key)
        existing.append({
            "date": row["date"],
            "commodity": row["commodity"],
            "location": row["location"],
            "delivery_period": row["delivery_period"],
            "cash": f"{row['cash']:.4f}",
            "futures_month": row["futures_month"],
            "futures": f"{row['futures']:.4f}",
            "basis": f"{row['basis']:.4f}",
        })
        added += 1
    with hist_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in existing:
            writer.writerow({field: row[field] for field in FIELDS})
    print(f"latest {len(rows)} bids, history +{added} (total {len(existing)})")


if __name__ == "__main__":
    main()
