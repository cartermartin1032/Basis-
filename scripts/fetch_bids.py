#!/usr/bin/env python3
"""Fetch Havana cash bids (corn/soybeans) from Western Grain Marketing's Barchart CSV export.
Usage: python3 fetch_bids.py [--through 2027-07] [--out file.csv]"""
import csv, io, sys, datetime, urllib.request, argparse
LOCS = {"ADM Havana": 86661, "Cargill Havana": 86675, "CHS Havana": 86670}  # WGM site IDs
URL = "https://www.westerngrainmarketing.com/markets/cashbid-download.php?l={}"
MON = {"F":"Jan","G":"Feb","H":"Mar","J":"Apr","K":"May","M":"Jun","N":"Jul","Q":"Aug","U":"Sep","V":"Oct","X":"Nov","Z":"Dec"}
def fut(s):  # "478-6" (cents, eighths) -> dollars
    w, _, e = s.partition("-"); return round((int(w) + int(e or 0)/8)/100, 4)
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--through", default="2027-07"); ap.add_argument("--out")
    a = ap.parse_args(); today = datetime.date.today().isoformat()
    out = a.out or f"bids_{today}.csv"; rows = []
    for loc, lid in LOCS.items():
        url = URL.format(lid)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        txt = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        for r in csv.DictReader(io.StringIO(txt)):
            if r["Commodity"] not in ("Corn", "Soybeans") or r["Delivery Start"][:7] > a.through: continue
            sym = r["Symbol"]; fm = f"{MON[sym[2]]} 20{sym[3:]}"
            d = datetime.date.fromisoformat(r["Delivery Start"])
            rows.append(dict(date=today, commodity=r["Commodity"], location=loc,
                delivery_period=f'{r["Delivery Start"]} to {r["Delivery End"]} ({d:%b %Y})',
                cash=float(r["Cash Price"].strip("$")), futures_month=f"{fm} ({sym})",
                futures=fut(r["Futures"]), basis=round(float(r["Basis"])/100, 4), source_url=url))
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} bids -> {out}")
if __name__ == "__main__": main()
