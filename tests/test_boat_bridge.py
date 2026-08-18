"""boat_bridge tests — the boat feeds the room (plan §3.7.1)."""
import math
from pathlib import Path

import pytest

from boat_bridge import BoatBridge, NmeaDay, parse_nmea


def _nmea(body: str) -> str:
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"${body}*{c:02X}"


def test_parse_rmc():
    s = _nmea("GPRMC,061200.00,A,5701.1234,N,13430.5678,W,7.2,185.6,180826,,")
    f = parse_nmea(s)
    assert f and f["sensor"] == "nav"
    assert f["speed"] == 7.2 and abs(f["heading"] - 185.6) < 0.01


def test_parse_dbt():
    s = _nmea("SDDBT,212.5,f,64.8,M,,")
    f = parse_nmea(s)
    assert f and f["sensor"] == "depth_ft" and f["depth"] == 212.5


def test_bad_checksum_rejected():
    assert parse_nmea("$GPRMC,061200.00,A,5701.1234,N*FF") is None
    assert parse_nmea("not nmea") is None


def test_replay_day(tmp_path):
    lines = [_nmea(f"GPRMC,{i:06d}.00,A,5701.1,N,13430.5,W,7.{i%10},18{i%3}0,180826,,")
             for i in range(30)]
    lines += [_nmea(f"SDDBT,{200+(i%7)*3}.1,f,64.0,M,,") for i in range(50)]
    log = tmp_path / "day.log"
    log.write_text("\n".join(lines))
    b = BoatBridge()
    n = b.replay(str(log))
    assert n == 80
    f = b.field()
    assert f["frames"] == 80
    assert 0.0 <= f["biomass_proxy"] <= 1.0
    assert b.last_nav and b.last_nav["heading"] is not None


def test_biomass_proxy_flat_bottom_reads_low():
    day = NmeaDay()
    for d in [200.0] * 20:
        day.feed({"sensor": "depth_ft", "depth": d})
    assert day.biomass_proxy() == 0.0


def test_biomass_proxy_structure_reads_high():
    day = NmeaDay()
    for i in range(40):
        day.feed({"sensor": "depth_ft", "depth": 200 + (i % 2) * 40})
    assert day.biomass_proxy() > 0.4


def test_summary_line():
    b = BoatBridge()
    b.day.feed({"sensor": "nav", "heading": 90.0, "speed": 8.0})
    for i in range(10):
        b.day.feed({"sensor": "depth_ft", "depth": 200 + (i % 2) * 40})
    line = b.field_summary_line()
    assert "EILEEN" in line and "heading" in line
