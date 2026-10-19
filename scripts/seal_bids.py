#!/usr/bin/env python3
"""Seal posted bids into data/bids.enc.json.

The passphrase comes only from the BIDS_PASSCODE environment variable (the
GitHub Actions secret). Latest bids and the full history are encrypted with
AES-256-GCM. The key is PBKDF2-HMAC-SHA256, 200000 iterations, a random
16-byte salt, and a random 12-byte IV. The file holds salt, iv, and
ciphertext only.

History lives inside that ciphertext. On later runs the previous file is
decrypted, new rows are appended (deduped by date + location + commodity +
delivery period), and the result is re-encrypted. The workflow calls this
only when BIDS_PASSCODE is set. A missing or empty passphrase still fails
here, before any plaintext is written.
"""
import base64
import csv
import datetime
import json
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 200_000
KDF = "PBKDF2-SHA256"
PLAINTEXT_NAMES = ("bids_latest.json", "bids_history.csv")
LOC_ORDER = ["ADM Havana", "Cargill Havana", "CHS Havana"]
SECRET_URL = "https://github.com/cartermartin1032/Basis-/settings/secrets/actions/new"


def fail(message):
    print(f"::error title=Posted bids not sealed::{message}", file=sys.stderr)
    sys.exit(1)


def num(value):
    return round(float(value), 4)


def sort_key(row):
    crop = {"Corn": 0, "Soybeans": 1}.get(row["commodity"], 2)
    try:
        loc = LOC_ORDER.index(row["location"])
    except ValueError:
        loc = 100
    return (crop, loc, row["location"], row["delivery_period"])


def derive_key(passcode, salt, iterations):
    if iterations < ITERATIONS:
        fail(f"refusing PBKDF2 with {iterations} iterations (minimum {ITERATIONS})")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passcode.encode("utf-8"))


def encrypt_payload(payload, passcode):
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = derive_key(passcode, salt, ITERATIONS)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(iv, body, None)
    return {
        "v": 1,
        "kdf": KDF,
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_file(path, passcode):
    try:
        blob = json.loads(path.read_text())
        if blob.get("v") != 1 or blob.get("kdf") != KDF:
            fail(f"{path} is not a PBKDF2-SHA256 AES-GCM bid file")
        iterations = int(blob["iterations"])
        salt = base64.b64decode(blob["salt"])
        iv = base64.b64decode(blob["iv"])
        ciphertext = base64.b64decode(blob["ciphertext"])
        key = derive_key(passcode, salt, iterations)
        plain = AESGCM(key).decrypt(iv, ciphertext, None)
        return json.loads(plain.decode("utf-8"))
    except InvalidTag:
        fail(f"could not decrypt {path} with BIDS_PASSCODE. History was left unchanged.")
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"could not read {path} ({exc.__class__.__name__}). History was left unchanged.")


def bid_row(raw):
    try:
        return {
            "date": raw["date"],
            "commodity": raw["commodity"],
            "location": raw["location"],
            "delivery_period": raw["delivery_period"],
            "cash": num(raw["cash"]),
            "futures_month": raw.get("futures_month") or "",
            "futures": num(raw["futures"]),
            "basis": num(raw["basis"]) if raw.get("basis") not in (None, "") else num(num(raw["cash"]) - num(raw["futures"])),
        }
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"bad bid row: {exc.__class__.__name__}")


def hist_key(row):
    return (row["date"], row["location"], row["commodity"], row["delivery_period"])


def load_history_csv(path):
    rows = []
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            if not raw or not (raw.get("date") or "").strip():
                continue
            rows.append(bid_row(raw))
    return rows


def append_new(history, rows):
    seen = {hist_key(row) for row in history}
    added = 0
    for row in rows:
        key = hist_key(row)
        if key in seen:
            continue
        seen.add(key)
        history.append(row)
        added += 1
    return added


def read_passcode():
    passcode = os.environ.get("BIDS_PASSCODE", "")
    if not passcode or not passcode.strip():
        fail(
            "BIDS_PASSCODE is not set. Refusing to publish plaintext bids. "
            f"Add a repository Actions secret named BIDS_PASSCODE at {SECRET_URL}"
        )
    return passcode


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: seal_bids.py fetch.csv [data_dir]")
    passcode = read_passcode()
    src = Path(sys.argv[1])
    data_dir = Path(sys.argv[2]) if len(sys.argv) == 3 else Path("data")
    data_dir.mkdir(exist_ok=True)
    enc_path = data_dir / "bids.enc.json"

    with src.open(newline="") as handle:
        fetched = list(csv.DictReader(handle))
    rows = [bid_row(raw) for raw in fetched if raw and (raw.get("date") or "").strip()]
    if not rows:
        fail("no bids in fetch; leaving published data unchanged")
    rows.sort(key=sort_key)

    meta_path = src.with_suffix(".meta.json")
    notes, sources = [], {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        notes = list(meta.get("notes") or [])
        sources = dict(meta.get("sources") or {})

    history = []
    if enc_path.exists() and enc_path.stat().st_size:
        previous = decrypt_file(enc_path, passcode)
        for raw in previous.get("history") or []:
            history.append(bid_row(raw))
    hist_path = data_dir / "bids_history.csv"
    if hist_path.exists() and hist_path.stat().st_size:
        append_new(history, load_history_csv(hist_path))
    added = append_new(history, rows)

    fetched_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "fetched_at": fetched_at,
        "notes": notes,
        "sources": sources,
        "bids": rows,
        "history": history,
    }
    blob = encrypt_payload(payload, passcode)
    tmp = enc_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=2) + "\n")
    tmp.replace(enc_path)

    removed = []
    for name in PLAINTEXT_NAMES:
        path = data_dir / name
        if path.exists():
            path.unlink()
            removed.append(name)
    print(f"sealed {len(rows)} bids, history +{added} (total {len(history)}) -> {enc_path}")
    if removed:
        print("removed plaintext " + " ".join(removed))


if __name__ == "__main__":
    main()
