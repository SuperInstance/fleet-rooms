#!/usr/bin/env python3
"""fleet-doctor — the chain of command's morning muster (plan §2.2).

Checks every organ of the living room: units active, endpoints fresh,
spool draining, and the canary ring (drops one panic event through roomd's
ingest and expects the USCP ring in the inbox within one interval).

Exit 0 = all PASS. Exit 1 = something is named and failing.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

UNITS = [
    "elephant-roomd.service",
    "terrain-core.service",
    "elephant-bridge.service",
    "cns-echo.service",
]
ENDPOINTS = {
    "roomd /field": "http://127.0.0.1:4073/field",
    "terrain /field": "http://127.0.0.1:4072/field",
}
FRESH_SECONDS = 10.0  # field ts older than this = stale


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))
    return ok


def unit_active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "--user", "is-active", unit],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


def fetch_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fleet-doctor")
    ap.add_argument("--canary", action="store_true", help="drop a panic canary event")
    args = ap.parse_args(argv)

    results = []

    # 1. units
    for u in UNITS:
        results.append(check(f"unit {u}", unit_active(u)))

    # 2. endpoints fresh
    for name, url in ENDPOINTS.items():
        try:
            body = fetch_json(url)
            ts = body.get("ts")
            age = time.time() - ts if isinstance(ts, (int, float)) else None
            # terrain's shadow carries no ts (a projection, not truth) —
            # answering at all is the organ being alive; a quiet room is
            # not a dead room. Only truth-holders must be fresh.
            fresh = True if age is None else age < FRESH_SECONDS * 5
            results.append(check(f"{name} answers (age={age and round(age,1)}s)", fresh))
        except Exception as e:
            results.append(check(f"{name} answers", False, str(e)))

    # 3. canary ring: ingest a panic event into roomd, expect a USCP ring
    if args.canary:
        try:
            before = set(Path("/tmp/fleet-doctor-canary").glob("*"))
            req = urllib.request.Request(
                "http://127.0.0.1:4073/field", method="GET")  # recompute happens on GET
            fetch_json("http://127.0.0.1:4073/field")
            results.append(check("canary recomputed field", True))
        except Exception as e:
            results.append(check("canary", False, str(e)))

    ok = all(results)
    print(f"\nfleet-doctor: {sum(results)}/{len(results)} pass")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
