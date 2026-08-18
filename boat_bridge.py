#!/usr/bin/env python3
"""boat_bridge — the boat feeds the room (plan §3.7.1).

NMEA 0183 ($GPRMC / $GGA / $SDDBT / $GPHDT) → BoatHarness → field.
The harness's sensor dials (radar coherence, sounder biomass, nav) read
the day; this bridge ships their numbers to roomd as a room named after
the vessel, so the boat's field flows through the same /field contract
as every chat room. Numbers, not feeds (fleet-dynamics doctrine).

v0 honest gap (labeled, per plan): $SDDBT is depth, not biomass — we ship
a documented proxy (bottom-depth variance deviation) until the fish-finder
sentence arrives.

Usage:
    python3 boat_bridge.py --replay nmea-day.log [--port 4074] [--post-to http://127.0.0.1:4073/ingest]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "elephant"))

from elephant.harness import BoatHarness  # noqa: E402

__all__ = ["parse_nmea", "NmeaDay", "BoatBridge"]


# --------------------------------------------------------------------- #
# NMEA parsing (checksum-verified; mirrors cns-bridge's sentence set)    #
# --------------------------------------------------------------------- #
def _checksum_ok(sentence: str) -> bool:
    body = sentence.strip()
    if not body.startswith("$") or "*" not in body:
        return False
    payload, cs = body[1:].rsplit("*", 1)
    try:
        want = int(cs[:2], 16)
    except ValueError:
        return False
    return want == (0 if not payload else __import__("functools").reduce(lambda a, c: a ^ ord(c), payload, 0))


def parse_nmea(line: str) -> Optional[Dict]:
    """One NMEA sentence → a normalized frame dict, or None."""
    line = line.strip()
    if not line.startswith("$") or not _checksum_ok(line):
        return None
    fields = line[1:].split("*")[0].split(",")
    kind = fields[0]
    if kind.endswith("RMC") and len(fields) > 7 and fields[7]:     # $xxRMC
        try:
            return {"sensor": "nav",
                    "heading": float(fields[8] or 0),               # course over ground
                    "speed": float(fields[7])}                      # knots
        except ValueError:
            return None
    if kind.endswith("HDT") and len(fields) > 1 and fields[1]:      # true heading
        try:
            return {"sensor": "nav", "heading": float(fields[1]), "speed": None}
        except ValueError:
            return None
    if kind.endswith("DBT") and len(fields) > 1 and fields[1]:      # depth below transducer, feet
        try:
            return {"sensor": "depth_ft", "depth": float(fields[1])}
        except ValueError:
            return None
    return None


class NmeaDay:
    """Accumulates one replayed day: nav frames + the depth series."""

    def __init__(self) -> None:
        self.nav: List[Dict] = []
        self.depths: List[float] = []

    def feed(self, frame: Dict) -> None:
        if frame["sensor"] == "nav":
            self.nav.append(frame)
        elif frame["sensor"] == "depth_ft":
            self.depths.append(frame["depth"])

    def biomass_proxy(self) -> float:
        """$SDDBT depth-variance proxy, documented as a proxy.

        Good fishing days (per the fleet's inductive anchors) show tighter
        bottom structure along the drag; a flat variance reads low, a
        volatile bottom reads higher. [0, 1], saturation at 40 ft of stdev.
        """
        if len(self.depths) < 4:
            return 0.0
        mean = sum(self.depths) / len(self.depths)
        var = sum((d - mean) ** 2 for d in self.depths) / len(self.depths)
        return min(1.0, math.sqrt(var) / 40.0)


class BoatBridge:
    def __init__(self, name: str = "EILEEN", post_to: Optional[str] = None):
        self.harness = BoatHarness(name=name)
        self.day = NmeaDay()
        self.post_to = post_to
        self.last_nav: Optional[Dict] = None

    def ingest_sentence(self, line: str) -> bool:
        frame = parse_nmea(line)
        if frame is None:
            return False
        self.day.feed(frame)
        if frame["sensor"] == "nav":
            self.last_nav = frame
            if frame.get("speed") is not None:
                self.harness.ingest_nav(frame["heading"], frame["speed"])
        elif frame["sensor"] == "depth_ft":
            self.harness.ingest_sounder(self.day.biomass_proxy())
        return True

    def replay(self, path: str) -> int:
        n = 0
        for line in Path(path).read_text().splitlines():
            if self.ingest_sentence(line):
                n += 1
        return n

    def field(self) -> Dict:
        """The boat's field, as roomd-shaped numbers."""
        room = self.harness.signal_room if hasattr(self.harness, "signal_room") else None
        out: Dict = {"vessel": self.harness.name, "ts": time.time(),
                     "frames": len(self.day.nav) + len(self.day.depths),
                     "biomass_proxy": round(self.day.biomass_proxy(), 4),
                     "last_nav": self.last_nav}
        if room is not None:
            out["message_count"] = len(room.messages)
        return out

    def post_field(self) -> bool:
        if not self.post_to:
            return False
        body = json.dumps({"room": f"the-boat-{self.harness.name}",
                           "author": "boat-bridge",
                           "text": self.field_summary_line(),
                           "ts": time.time()}).encode()
        req = urllib.request.Request(self.post_to, data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        return True

    def field_summary_line(self) -> str:
        """The boat as one line of chatter the elephant can read."""
        nav = self.last_nav or (self.day.nav[-1] if self.day.nav else {})
        hdg = nav.get("heading")
        spd = nav.get("speed")
        bio = self.day.biomass_proxy()
        parts = []
        if hdg is not None:
            parts.append(f"heading {hdg:.0f}")
        if spd is not None:
            parts.append(f"{spd:.1f} kts")
        parts.append("good marks on the sounder!" if bio > 0.5
                     else "marks thin today" if bio > 0.15
                     else "searching, nothing showing")
        return "F/V EILEEN: " + ", ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="boat_bridge")
    ap.add_argument("--replay", help="NMEA log file to replay")
    ap.add_argument("--post-to", default=None, help="roomd /ingest endpoint")
    ap.add_argument("--serve", type=int, default=None, metavar="PORT",
                    help="serve GET /field on this port")
    args = ap.parse_args(argv)
    b = BoatBridge(post_to=args.post_to)
    if args.replay:
        n = b.replay(args.replay)
        print(f"boat_bridge: {n} sentences ingested", flush=True)
        print(json.dumps(b.field(), indent=2))
        if args.post_to:
            b.post_field()
            print("boat_bridge: field posted to roomd", flush=True)
    if args.serve:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps(b.field()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        ThreadingHTTPServer(("127.0.0.1", args.serve), H).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
