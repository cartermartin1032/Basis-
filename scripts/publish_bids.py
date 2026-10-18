#!/usr/bin/env python3
"""Plaintext bid publishing is disabled.

Posted bids are sealed by scripts/seal_bids.py into data/bids.enc.json using
the BIDS_PASSCODE environment variable. This script refuses to write
data/bids_latest.json or data/bids_history.csv.
"""
import sys

sys.exit(
    "plaintext publishing is disabled. Run scripts/seal_bids.py with "
    "BIDS_PASSCODE set. Refusing to write bids_latest.json or bids_history.csv."
)
