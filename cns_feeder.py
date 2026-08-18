#!/usr/bin/env python3
"""cns_feeder — the bus feeds the room (real chatter for the corpus).

Watches the CNS inbox/outbox dirs and POSTs every USCP packet into roomd
as room chatter, so the elephant reads the fleet's actual conversation —
not just canaries and the boat. Room name = the packet's target/subject
origin, mapped to a small set of standing rooms (default: "the-bridge").

This is what makes the v3 contrast corpus real: warm Tap sessions, terse
ops traffic, Wesley's earnest replies — the field log finally sees range.

Usage:
    python3 cns_feeder.py --post-to http://127.0.0.1:4073/ingest \
        [--watch ~/.hermes/cns_outbox] [--interval 2]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Set

import urllib.request

__all__ = ["packet_to_chatter", "FeedState", "one_pass"]


def packet_to_chatter(packet: Dict) -> Optional[Dict]:
    """USCP packet (v1 or v3) → roomd /ingest event (or None if contentless)."""
    header = packet.get("header", {}) if isinstance(packet, dict) else {}
    body = packet.get("body", {}) if isinstance(packet, dict) else {}
    author = str(header.get("origin_id", "anon")) or "anon"
    # v1: body.intent / body.subject / body.content
    # v3: body.intent / body.payload.detail (payload may hold richer keys)
    intent = str(body.get("intent", header.get("intent", ""))).strip()
    content = str(body.get("content", "")).strip()
    subject = str(body.get("subject", "")).strip()
    payload = body.get("payload", {})
    if not content and isinstance(payload, dict):
        # v3: richest text first — detail > status+detail > whole payload
        detail = str(payload.get("detail", "")).strip()
        status = str(payload.get("status", "")).strip()
        if detail and status and status.lower() not in detail.lower():
            content = f"{status}: {detail}"
        else:
            content = detail or status or " ".join(
                f"{k}={v}" for k, v in list(payload.items())[:4])
    if not content and not subject:
        return None
    text = content or subject
    if intent:
        text = f"[{intent}] {text}"
    # standing room: ops traffic reads in one room; keep it simple and
    # bounded — a room per packet would explode the registry.
    return {"room": "the-bridge", "author": author, "text": text[:2000],
            "ts": time.time()}


class FeedState:
    def __init__(self) -> None:
        self.seen: Set[str] = set()
        self.fed = 0

    def _mark(self, path: Path) -> bool:
        key = f"{path.name}:{path.stat().st_mtime_ns}"
        if key in self.seen:
            return False
        self.seen.add(key)
        if len(self.seen) > 4096:  # bounded
            self.seen = set(sorted(self.seen)[-2048:])
        return True


def one_pass(watch_dirs, post_to: str, state: FeedState) -> int:
    n = 0
    for wd in watch_dirs:
        wd = Path(wd)
        if not wd.is_dir():
            continue
        for f in sorted(wd.glob("*.json")):
            if not state._mark(f):
                continue
            try:
                packet = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            event = packet_to_chatter(packet)
            if event is None:
                continue
            req = urllib.request.Request(
                post_to, data=json.dumps(event).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=3).read()
                n += 1
                state.fed += 1
            except Exception:
                pass  # roomd down → skip this file's re-feed via seen-set
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cns_feeder")
    ap.add_argument("--post-to", default="http://127.0.0.1:4073/ingest")
    ap.add_argument("--watch", nargs="+",
                    default=["~/.hermes/cns_outbox", "~/.hermes/cns_inbox"])
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    watch = [Path(p).expanduser() for p in args.watch]
    state = FeedState()
    while True:
        n = one_pass(watch, args.post_to, state)
        if n:
            print(f"cns_feeder: {n} packets → roomd (total {state.fed})",
                  flush=True)
        if args.once:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
