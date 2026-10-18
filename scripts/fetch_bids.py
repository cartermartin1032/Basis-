#!/usr/bin/env python3
"""Fetch Havana corn and soybean cash bids.

ADM Havana comes from ADM's public Gradable page
(https://adm.gradable.com/market/Havana--IL), which loads
/api/commodities/merchandising/bootstrap and
/api/commodities/v2/merchandising/instruments/market/{id}?offer_type=public.

Cargill Havana comes from the Barchart cashbids JSON embedded on
https://www.cargillag.com/locations/havana-cah (location 64026).

CHS Havana stays on Western Grain Marketing's CSV export. If Gradable or
Cargill cannot be fetched, that elevator falls back to Western Grain and a
note is written for the app.

Usage: python3 fetch_bids.py [--through 2027-10] [--out file.csv]
"""
import argparse
import calendar
import csv
import datetime
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

WGM_URL = "https://www.westerngrainmarketing.com/markets/cashbid-download.php?l={}"
WGM_LOCS = {"ADM Havana": 86661, "Cargill Havana": 86675, "CHS Havana": 86670}
CARGILL_PAGE = "https://www.cargillag.com/locations/havana-cah"
CARGILL_API = "https://d96y3rjfk5o7l.cloudfront.net/"
CARGILL_LOCATION = "64026"
ADM_PAGE = "https://adm.gradable.com/market/Havana--IL"
ADM_BOOTSTRAP = "https://adm.gradable.com/api/commodities/merchandising/bootstrap"
ADM_INSTRUMENTS = "https://adm.gradable.com/api/commodities/v2/merchandising/instruments/market/{}"
BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json,text/csv,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MON = {"F": "Jan", "G": "Feb", "H": "Mar", "J": "Apr", "K": "May", "M": "Jun",
       "N": "Jul", "Q": "Aug", "U": "Sep", "V": "Oct", "X": "Nov", "Z": "Dec"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FIELDS = ["date", "commodity", "location", "delivery_period", "cash", "futures_month", "futures", "basis", "source_url"]


def http_text(url, referer=None, timeout=40):
    headers = dict(BROWSER)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace"), resp.geturl()


def http_json(url, referer=None):
    text, final = http_text(url, referer=referer)
    if text.startswith("while(1);"):
        text = text[len("while(1);"):]
    return json.loads(text), final


def yy(year):
    return f"{year % 100:02d}"


def expand_year(raw):
    """Turn a futures year token into a full year. ZCZ6 -> 2026, ZCZ26 -> 2026."""
    n = int(raw)
    if n >= 100:
        return n
    if n >= 10:
        return 2000 + n
    today = datetime.date.today().year
    year = (today // 10) * 10 + n
    if year < today - 1:
        year += 10
    return year


def futures_label(symbol):
    """Map ZCZ26, ZCZ6, CZ6, or SX6 to 'Dec 2026 (ZCZ6)'."""
    sym = (symbol or "").strip()
    if len(sym) >= 4 and sym[:2] in ("ZC", "ZS", "ZW") and sym[2] in MON:
        month, year = MON[sym[2]], expand_year(sym[3:])
    elif len(sym) >= 3 and sym[1] in MON and sym[2:].isdigit():
        month, year = MON[sym[1]], expand_year(sym[2:])
    else:
        return sym
    return f"{month} {year} ({sym})"


def period_label(start, end):
    """Readable delivery name: FH/LH half months, full months, or Sep-Oct spans."""
    def last_day(day):
        return calendar.monthrange(day.year, day.month)[1]

    if (start.day == 1 and end.day == last_day(end)
            and (end.year * 12 + end.month) == (start.year * 12 + start.month + 1)):
        if start.year == end.year:
            return f"{MONTHS[start.month - 1]}-{MONTHS[end.month - 1]} {yy(end.year)}"
        return f"{MONTHS[start.month - 1]} {yy(start.year)}-{MONTHS[end.month - 1]} {yy(end.year)}"
    if start.year == end.year and start.month == end.month:
        name = f"{MONTHS[start.month - 1]} {yy(start.year)}"
        if start.day == 1 and end.day == last_day(end):
            return name
        if start.day == 1 and end.day == 15:
            return f"FH {name}"
        if start.day >= 16 and end.day == last_day(end):
            return f"LH {name}"
    return f"{start.month}/{start.day}/{yy(start.year)}-{end.month}/{end.day}/{yy(end.year)}"


def delivery_period(start, end, label):
    return f"{start.isoformat()} to {end.isoformat()} ({label})"


def within(start, through):
    return start.strftime("%Y-%m") <= through


def crop_name(raw):
    text = (raw or "").lower()
    if "soy" in text:
        return "Soybeans"
    if "corn" in text:
        return "Corn"
    return None


def row(today, commodity, location, start, end, label, cash, futures_month, futures, basis, source):
    return {
        "date": today,
        "commodity": commodity,
        "location": location,
        "delivery_period": delivery_period(start, end, label),
        "cash": round(float(cash), 4),
        "futures_month": futures_month,
        "futures": round(float(futures), 4),
        "basis": round(float(basis), 4),
        "source_url": source,
    }


def wgm_futures_price(text):
    """'478-6' (cents and eighths) -> dollars."""
    whole, _, eighth = str(text).partition("-")
    return round((int(whole) + int(eighth or 0) / 8) / 100, 4)


def fetch_wgm(location, lid, through, today):
    url = WGM_URL.format(lid)
    text, _ = http_text(url, referer="https://www.westerngrainmarketing.com/")
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        commodity = crop_name(rec.get("Commodity"))
        if not commodity:
            continue
        start = datetime.date.fromisoformat(rec["Delivery Start"])
        end = datetime.date.fromisoformat(rec["Delivery End"])
        if not within(start, through):
            continue
        sym = rec["Symbol"]
        rows.append(row(
            today, commodity, location, start, end, period_label(start, end),
            rec["Cash Price"].strip().lstrip("$"), futures_label(sym),
            wgm_futures_price(rec["Futures"]), float(rec["Basis"]) / 100, url,
        ))
    return rows


def fetch_cargill(through, today):
    query = urllib.parse.urlencode({
        "module": "cashbids",
        "location": CARGILL_LOCATION,
        "output": "json",
        "commOverviewByLocation": "1",
        "commRoots": ",ZS,ZC",
    })
    data, _ = http_json(CARGILL_API + "?" + query, referer=CARGILL_PAGE)
    rows = []
    for group in data.get("bigGroups") or []:
        commodity = crop_name(group.get("name"))
        if not commodity:
            continue
        for bid in group.get("cashbids") or []:
            start = datetime.datetime.strptime(bid["delivery_start"], "%m/%d/%Y").date()
            end = datetime.datetime.strptime(bid["delivery_end"], "%m/%d/%Y").date()
            if not within(start, through):
                continue
            rows.append(row(
                today, commodity, "Cargill Havana", start, end, period_label(start, end),
                bid["flatprice"], futures_label(bid.get("futuresymbol")),
                bid["futuresprice"], bid["basis"], CARGILL_PAGE,
            ))
    if not rows:
        raise RuntimeError("Cargill cashbids response had no corn or soybean rows")
    return rows


def adm_market_id(data):
    matches = []
    for market in data.get("markets") or []:
        address = market.get("address") or {}
        city = (address.get("input_locality") or address.get("city") or "").lower()
        state = (address.get("input_administrative_area_level_1") or "").lower()
        display = (market.get("display_name") or "").lower()
        if "havana" in city or display.startswith("havana"):
            if state in ("illinois", "il", "") or "il" in display:
                matches.append(market)
    public = [m for m in matches if m.get("enable_gradable_public_site")]
    chosen = (public or matches)
    if not chosen:
        raise RuntimeError("Havana was not in the Gradable bootstrap market list")
    return chosen[0]["id"]


def fetch_adm(through, today):
    bootstrap, _ = http_json(ADM_BOOTSTRAP, referer=ADM_PAGE)
    market_id = adm_market_id(bootstrap)
    url = ADM_INSTRUMENTS.format(market_id) + "?" + urllib.parse.urlencode({"offer_type": "public"})
    data, _ = http_json(url, referer=ADM_PAGE)
    crops = {}
    for crop in data.get("crops") or []:
        crops[str(crop.get("commodity"))] = crop_name(crop.get("crm_display_name"))
    rows = []
    for inst in data.get("instruments") or []:
        if inst.get("deleted"):
            continue
        commodity = crops.get(str(inst.get("ext_commodity_id"))) or crop_name(inst.get("display_name"))
        if not commodity:
            continue
        start = datetime.datetime.fromtimestamp(inst["delivery_period_start"], datetime.timezone.utc).date()
        end = datetime.datetime.fromtimestamp(inst["delivery_period_end"], datetime.timezone.utc).date()
        if not within(start, through):
            continue
        futures = inst.get("futures") or {}
        symbols = futures.get("symbols") or {}
        price = ((futures.get("pricing") or {}).get("delayed") or {}).get("price")
        if price is None:
            price = inst.get("futures_bid")
        label = (inst.get("display_name") or "").strip() or period_label(start, end)
        rows.append(row(
            today, commodity, "ADM Havana", start, end, label,
            inst["cash_bid"], futures_label(symbols.get("display") or symbols.get("cme_globex") or ""),
            price, inst["basis_bid"], ADM_PAGE,
        ))
    if not rows:
        raise RuntimeError("Gradable instruments response had no corn or soybean rows")
    return rows


def load_location(name, primary, fallback_lid, through, today, notes, sources, lag_note):
    try:
        found = primary()
        sources[name] = found[0]["source_url"] if found else ""
        print(f"{name}: {len(found)} bids from {sources[name]}", file=sys.stderr)
        return found
    except Exception as exc:
        print(f"{name}: primary source failed ({exc}); falling back to Western Grain", file=sys.stderr)
        try:
            found = fetch_wgm(name, fallback_lid, through, today)
        except Exception as fallback_exc:
            print(f"{name}: Western Grain fallback failed ({fallback_exc})", file=sys.stderr)
            notes.append(f"{lag_note} (Western Grain fallback also failed: {fallback_exc})")
            return []
        sources[name] = WGM_URL.format(fallback_lid)
        notes.append(lag_note)
        print(f"{name}: {len(found)} bids from Western Grain fallback", file=sys.stderr)
        return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--through", default="2027-10")
    parser.add_argument("--out")
    args = parser.parse_args()
    today = datetime.date.today().isoformat()
    out = args.out or f"bids_{today}.csv"
    notes = []
    sources = {}
    rows = []
    rows += load_location(
        "ADM Havana", lambda: fetch_adm(args.through, today), WGM_LOCS["ADM Havana"],
        args.through, today, notes, sources, "ADM source: WGM feed, may lag ADM Gradable",
    )
    rows += load_location(
        "Cargill Havana", lambda: fetch_cargill(args.through, today), WGM_LOCS["Cargill Havana"],
        args.through, today, notes, sources, "Cargill source: WGM feed, may lag Cargill.com",
    )
    try:
        chs = fetch_wgm("CHS Havana", WGM_LOCS["CHS Havana"], args.through, today)
        sources["CHS Havana"] = WGM_URL.format(WGM_LOCS["CHS Havana"])
        print(f"CHS Havana: {len(chs)} bids from Western Grain", file=sys.stderr)
        rows += chs
    except Exception as exc:
        print(f"CHS Havana: Western Grain failed ({exc})", file=sys.stderr)
        notes.append(f"CHS Havana bids unavailable ({exc})")
    if not rows:
        sys.exit("no bids fetched")
    rows.sort(key=lambda r: (r["commodity"], r["location"], r["delivery_period"]))
    with open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    meta = out[:-4] + ".meta.json" if out.endswith(".csv") else out + ".meta.json"
    with open(meta, "w") as handle:
        json.dump({"notes": notes, "sources": sources}, handle, indent=2)
        handle.write("\n")
    print(f"{len(rows)} bids -> {out}")
    if notes:
        print("NOTES: " + " | ".join(notes))


if __name__ == "__main__":
    main()
