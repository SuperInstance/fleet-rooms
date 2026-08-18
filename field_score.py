#!/usr/bin/env python3
"""field_score — the room's field, one bar at a time.

Reads the elephant roomd field (`GET /field` on :4073) and emits JSONL MIDI
events, one file per bar, into a spool dir that fleet-audio's
`--spool` reader consumes. Plan §1.4 item 2 (fleet-next-level-plan.md).

JSONL schema — confirmed against fleet-audio `src/midi.rs` (MidiEvent)
and `src/io/jsonl_spool.rs` (JsonlSpoolReader, files consumed + deleted,
sorted by filename so bars must sort by emission order):

    {"timestamp_us": <u64>, "channel": <u8>, "note": <u8>, "velocity": <u8>}

  - timestamp_us: microseconds since renderer start; we tick monotonically.
  - velocity 0 == note-off; every note_on gets a note_off inside its bar.
  - channel voice map (midi.rs VoiceType::from_channel):
      0 piano · 1 bass · 2 strings · 3 guitar · 9 drums

Mapping v0 (deliberate and simple, per plan):
  - warmth        → pentatonic scale-degree center + velocity floor
  - presence      → string-pad (ch 2) note density, 1..8 per bar
  - volume        → velocity (stacks on the warmth floor)
  - panic >= 0.5  → drums (ch 9) hit rate up, tempo ×1.15, minor pentatonic
  - joke_landing > 0.5 → guitar (ch 3) pluck flourish in the last beat

Field shape (roomd §1.1): {"warmth": f, "kappa": f,
                          "dials": {"mood", "volume", "earnestness",
                                    "cynicism", "joke_landing",
                                    "panic", "presence"},
                          "map_temperature": ...}
Missing dials fall back to the elephant's neutral defaults
(presence 0.5, the rest 0). Unknown/extra keys are ignored.

If the field endpoint is down we log to stderr and skip the bar —
a cold room is not a dead room (and never a crashed scorer).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import urlopen

DEFAULT_FIELD_URL = "http://127.0.0.1:4073/field"
US_PER_MINUTE = 60_000_000
BEATS_PER_BAR = 4
DEFAULT_BPM = 96.0

# Channels (midi.rs VoiceType::from_channel)
CH_MELODY = 0   # piano
CH_PAD = 2      # strings
CH_GUITAR = 3   # guitar
CH_DRUMS = 9    # drums (channel 10, 0-indexed)

# General MIDI drum map (subset)
DRUM_KICK = 36
DRUM_SNARE = 38
DRUM_HAT = 42

# Two-octave pools built on middle C
MELODY_BASE = 60
MAJOR_PENT = (0, 2, 4, 7, 9)
MINOR_PENT = (0, 3, 5, 7, 10)

PANIC_THRESHOLD = 0.5      # elephant's PANIC_HI convention
JOKE_THRESHOLD = 0.5
PANIC_TEMPO_MULT = 1.15

EVENT_KEYS = ("timestamp_us", "channel", "note", "velocity")


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def read_dial(dials: Dict[str, Any], name: str, default: float,
              lo: float, hi: float) -> float:
    try:
        raw = float(dials.get(name, default))
    except (TypeError, ValueError):
        raw = default
    return clamp(raw, lo, hi)


def scale_pool(scale: Tuple[int, ...], base: int = MELODY_BASE) -> List[int]:
    """Two octaves plus the top octave note: 11 tones, ascending."""
    tones = [base + iv for iv in scale]
    tones += [base + 12 + iv for iv in scale]
    tones.append(base + 24)
    return tones


def bar_microseconds(bpm: float) -> int:
    return int(round(US_PER_MINUTE * BEATS_PER_BAR / bpm))


def _event(ts: int, ch: int, note: int, vel: int) -> Dict[str, int]:
    return {"timestamp_us": int(ts), "channel": int(ch),
            "note": int(note), "velocity": int(vel)}


def _note(events: List[Dict[str, int]], ch: int, note: int, vel: int,
          on_us: int, off_us: int, bar_us: int) -> None:
    """A note_on/note_off pair, clamped inside the bar."""
    on_us = max(0, min(int(on_us), bar_us - 2))
    off_us = max(on_us + 1, min(int(off_us), bar_us - 1))
    events.append(_event(on_us, ch, note, vel))
    events.append(_event(off_us, ch, note, 0))


def compose_bar(field: Dict[str, Any], bar_index: int, start_us: int,
                bpm: float = DEFAULT_BPM) -> Tuple[List[Dict[str, int]], int]:
    """One bar of the room, as schema-exact MidiEvent dicts.

    Returns (events_sorted_by_timestamp, bar_us). Pure function of
    (field, bar_index, start_us, bpm) — the RNG is seeded per bar.
    """
    try:
        warmth = clamp(float(field.get("warmth", 0.0)), -1.0, 1.0)
    except (TypeError, ValueError):
        warmth = 0.0
    dials = field.get("dials") or {}
    if not isinstance(dials, dict):
        dials = {}
    volume = read_dial(dials, "volume", 0.0, 0.0, 1.0)
    presence = read_dial(dials, "presence", 0.5, 0.0, 1.0)
    panic = read_dial(dials, "panic", 0.0, 0.0, 1.0)
    joke = read_dial(dials, "joke_landing", 0.0, -1.0, 1.0)

    panic_mode = panic >= PANIC_THRESHOLD
    bpm_bar = bpm * PANIC_TEMPO_MULT if panic_mode else bpm
    bar_us = bar_microseconds(bpm_bar)
    eighth = max(1, bar_us // 8)
    sixteenth = max(1, bar_us // 16)

    rng = random.Random(bar_index)
    events: List[Dict[str, int]] = []

    # -- warmth → scale degree + velocity floor ------------------------ #
    scale = MINOR_PENT if panic_mode else MAJOR_PENT
    pool = scale_pool(scale)
    t_warm = (warmth + 1.0) / 2.0                     # 0..1
    center = int(round(t_warm * (len(pool) - 1)))     # cold→low, warm→high
    vel_floor = 30 + int(round(24.0 * t_warm))        # 30..54

    # -- melody: piano (ch 0), volume → velocity ----------------------- #
    n_mel = 4 + rng.randrange(5)                      # 4..8 eighth slots
    mel_vel = max(1, min(127, vel_floor + int(round(72.0 * volume))))
    for i in range(n_mel):
        deg = max(0, min(len(pool) - 1,
                         center + rng.choice((-2, -1, 0, 0, 1, 2))))
        on = i * eighth + rng.randrange(0, max(1, eighth // 4))
        _note(events, CH_MELODY, pool[deg], mel_vel,
              on, on + bar_us // 4, bar_us)

    # -- presence → string pad (ch 2) density -------------------------- #
    n_pad = 1 + int(round(presence * 7.0))            # 1..8
    pad_vel = max(1, min(127, 24 + int(round(30.0 * volume))
                         + int(round(10.0 * t_warm))))
    pad_step = max(1, bar_us // n_pad)
    pad_root = max(0, pool[center] - 12)
    for i in range(n_pad):
        on = i * pad_step
        tone = pad_root + rng.choice((0, 7, 9 if scale is MAJOR_PENT else 10, 12))
        _note(events, CH_PAD, tone, pad_vel,
              on, on + pad_step - sixteenth, bar_us)

    # -- panic → drums (ch 9) hit rate --------------------------------- #
    hits = 4 + int(round(12.0 * panic))               # 4..16
    drum_vel = max(1, min(127, 40 + int(round(70.0 * panic))))
    drum_step = max(1, bar_us // hits)
    for i in range(hits):
        if panic_mode:
            drum = DRUM_KICK if i % 2 == 0 else DRUM_SNARE
        else:
            drum = DRUM_KICK if i % 4 == 0 else DRUM_HAT
        _note(events, CH_DRUMS, drum, drum_vel,
              i * drum_step, i * drum_step + max(1, sixteenth // 2), bar_us)

    # -- joke_landing > 0.5 → guitar (ch 3) pluck flourish ------------- #
    if joke > JOKE_THRESHOLD:
        g_vel = max(1, min(127, 70 + int(round(30.0 * volume))))
        base_deg = max(0, center - 1)
        t0 = bar_us - 4 * sixteenth                   # the last beat
        for k in range(5):
            deg = min(len(pool) - 1, base_deg + k)
            on = t0 + k * sixteenth
            _note(events, CH_GUITAR, pool[deg], g_vel,
                  on, on + max(1, sixteenth // 2), bar_us)

    events.sort(key=lambda e: e["timestamp_us"])
    for e in events:                                  # absolute ticks
        e["timestamp_us"] += start_us
    return events, bar_us


def event_line(event: Dict[str, int]) -> str:
    return json.dumps({k: event[k] for k in EVENT_KEYS}, separators=(",", ":"))


def write_bar(spool_dir: Path, seq: int, events: List[Dict[str, int]]) -> Path:
    """Write one bar's file atomically (temp + rename, responder pattern).

    Zero-padded sequence keeps JsonlSpoolReader's filename sort == emission
    order; it consumes (deletes) the file after reading.
    """
    spool_dir.mkdir(parents=True, exist_ok=True)
    path = spool_dir / f"bar_{seq:08d}.jsonl"
    fd, tmp = tempfile.mkstemp(dir=str(spool_dir), prefix=".bar_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for event in events:
                fh.write(event_line(event) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def fetch_field(url: str, timeout: float = 3.0) -> Dict[str, Any]:
    with urlopen(url, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"field at {url} is not a JSON object")
    return data


def load_field_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"field file {path} is not a JSON object")
    return data


def run(args: argparse.Namespace) -> int:
    spool_dir = Path(args.spool)
    seq = 0
    now_us = 0
    emitted = 0
    bars = args.bars if args.bars is not None else float("inf")
    while emitted < bars:
        try:
            field = (load_field_file(args.field_file) if args.field_file
                     else fetch_field(args.field))
        except (URLError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"field_score: field unavailable, skipping bar {seq}: {exc}",
                  file=sys.stderr)
            time.sleep(args.interval)
            continue
        events, bar_us = compose_bar(field, seq, now_us, bpm=args.bpm)
        if args.stdout:
            for event in events:
                print(event_line(event))
        else:
            write_bar(spool_dir, seq, events)
        now_us += bar_us
        seq += 1
        emitted += 1
        if emitted < bars:
            time.sleep(args.interval)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="field_score",
        description="the room's field, one bar at a time (plan §1.4)")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--field", default=DEFAULT_FIELD_URL,
                     help=f"field HTTP endpoint (default {DEFAULT_FIELD_URL})")
    src.add_argument("--field-file", default=None,
                     help="read the field from a JSON file instead of HTTP")
    parser.add_argument("--spool", default="spool",
                        help="spool dir for JSONL bar files (default ./spool)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="seconds between bars (default 2.0)")
    parser.add_argument("--bars", type=int, default=None,
                        help="stop after N bars (default: forever)")
    parser.add_argument("--bpm", type=float, default=DEFAULT_BPM,
                        help=f"base tempo (default {DEFAULT_BPM:g})")
    parser.add_argument("--stdout", action="store_true",
                        help="print events instead of writing the spool")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
