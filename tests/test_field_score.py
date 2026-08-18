"""field_score tests — fixture field → 8 bars well-formed (plan §1.4 verify)."""

import functools
import http.server
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import field_score  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
KEYS = {"timestamp_us", "channel", "note", "velocity"}


def load(name):
    return json.loads((FIXTURES / name).read_text())


def compose_sequence(fields, n=8, bpm=96.0):
    """Run compose_bar over n bars, ticking monotonically like run()."""
    events, bar_lens = [], []
    now_us = 0
    for i in range(n):
        field = fields[i % len(fields)]
        bar, bar_us = field_score.compose_bar(field, i, now_us, bpm=bpm)
        events.extend(bar)
        bar_lens.append(bar_us)
        now_us += bar_us
    return events, bar_lens, now_us


def assert_schema(events):
    for e in events:
        assert set(e) == KEYS
        assert all(isinstance(v, int) for v in e.values())
        assert 0 <= e["channel"] <= 15
        assert 0 <= e["note"] <= 127
        assert 0 <= e["velocity"] <= 127


def ons(events, ch):
    return [e for e in events if e["channel"] == ch and e["velocity"] > 0]


# -- 8 bars well-formed: the plan's acceptance shape ---------------------- #

def test_fixture_eight_bars_well_formed():
    events, bar_lens, total = compose_sequence([load("field_warm.json")], n=8)
    assert len(events) > 0
    assert_schema(events)

    # monotonic, in [0, total), first tick lands at 0
    ticks = [e["timestamp_us"] for e in events]
    assert ticks == sorted(ticks)
    assert ticks[0] == 0
    assert all(0 <= t < total for t in ticks)

    # steady tempo at 96 bpm: every bar the same length
    expected = field_score.bar_microseconds(96.0)
    assert bar_lens == [expected] * 8

    # every note_on is released by a note_off (voices don't hang)
    balance = {}
    for e in events:
        key = (e["channel"], e["note"])
        if e["velocity"] > 0:
            balance[key] = balance.get(key, 0) + 1
        else:
            balance[key] = balance.get(key, 0) - 1
    assert all(v == 0 for v in balance.values()), balance

    # the four mapped voices are present in a warm room
    channels = {e["channel"] for e in events}
    assert field_score.CH_MELODY in channels
    assert field_score.CH_PAD in channels
    assert field_score.CH_DRUMS in channels


def test_spool_files_parse_and_sort(tmp_path):
    fields = [load("field_warm.json"), load("field_panic.json")]
    now_us, paths = 0, []
    for i in range(8):
        field = fields[i % len(fields)]
        bar, bar_us = field_score.compose_bar(field, i, now_us)
        paths.append(field_score.write_bar(tmp_path, i, bar))
        now_us += bar_us

    files = sorted(p.name for p in tmp_path.glob("*.jsonl"))
    assert files == [f"bar_{i:08d}.jsonl" for i in range(8)]  # spool sort order
    assert [p.name for p in paths] == files

    all_events = []
    for path in sorted(tmp_path.glob("*.jsonl")):
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert lines
        for ln in lines:
            event = json.loads(ln)          # exact schema fleet-audio decodes
            assert set(event) == KEYS
            all_events.append(event)
    assert_schema(all_events)


def test_warm_then_panic_walk():
    """warm ×2 → panic ×2 → warm: ticks stay monotonic across the tempo jump."""
    fields = ([load("field_warm.json")] * 2
              + [load("field_panic.json")] * 2
              + [load("field_warm.json")] * 4)
    events, bar_lens, _ = compose_sequence(fields, n=8)
    assert_schema(events)
    ticks = [e["timestamp_us"] for e in events]
    assert ticks == sorted(ticks)
    # panic bars run ×1.15 tempo → shorter bars, exactly
    normal = field_score.bar_microseconds(96.0)
    panic = field_score.bar_microseconds(96.0 * 1.15)
    assert bar_lens == [normal, normal, panic, panic] + [normal] * 4


# -- the five mappings, one at a time -------------------------------------- #

def test_warmth_moves_degree_and_velocity_floor():
    def melody_notes(field):
        events, _, _ = compose_sequence([field], n=8)
        return ons(events, field_score.CH_MELODY)

    warm, cold = melody_notes(load("field_warm.json")), melody_notes(load("field_cold.json"))
    assert warm and cold
    assert (sum(n["note"] for n in warm) / len(warm)
            > sum(n["note"] for n in cold) / len(cold))
    assert (sum(n["velocity"] for n in warm) / len(warm)
            > sum(n["velocity"] for n in cold) / len(cold))


def test_panic_drums_rate_scale_and_tempo():
    neutral = load("field_neutral.json")
    panic = load("field_panic.json")
    neutral_events, _, _ = compose_sequence([neutral], n=4)
    panic_events, panic_lens, _ = compose_sequence([panic], n=4)

    # drum hit rate up
    assert len(ons(panic_events, field_score.CH_DRUMS)) > \
           len(ons(neutral_events, field_score.CH_DRUMS))

    # tempo ×1.15
    assert panic_lens[0] == field_score.bar_microseconds(96.0 * 1.15)

    # the scale dropped to minor pentatonic
    minor = set(field_score.scale_pool(field_score.MINOR_PENT))
    assert {e["note"] for e in ons(panic_events, field_score.CH_MELODY)} <= minor


def test_joke_landing_guitar_flourish():
    no_joke = load("field_neutral.json").copy()
    no_joke["dials"]["joke_landing"] = 0.3
    joke = load("field_joke.json")  # joke_landing 0.7

    plain_events, _, _ = compose_sequence([no_joke], n=4)
    joke_events, bar_lens, _ = compose_sequence([joke], n=4)

    assert ons(plain_events, field_score.CH_GUITAR) == []
    flourishes = ons(joke_events, field_score.CH_GUITAR)
    assert len(flourishes) == 5 * 4  # five plucks per bar, four bars

    # all in the last beat of the bar
    last_beat = bar_lens[0] - bar_lens[0] // 4
    bar0 = [e for e in joke_events if e["timestamp_us"] < bar_lens[0]
            and e["channel"] == field_score.CH_GUITAR and e["velocity"] > 0]
    assert bar0 and all(e["timestamp_us"] >= last_beat - 1 for e in bar0)


def test_presence_pads_density():
    def pad_ons(presence):
        field = {"warmth": 0.0, "dials": {"presence": presence, "volume": 0.3}}
        events, _, _ = compose_sequence([field], n=4)
        return len(ons(events, field_score.CH_PAD))

    assert pad_ons(0.9) > pad_ons(0.5) > pad_ons(0.1)


def test_volume_velocity():
    def mean_mel_vel(volume):
        field = {"warmth": 0.4, "dials": {"volume": volume, "presence": 0.5}}
        events, _, _ = compose_sequence([field], n=8)
        notes = ons(events, field_score.CH_MELODY)
        return sum(n["velocity"] for n in notes) / len(notes)

    assert mean_mel_vel(0.9) > mean_mel_vel(0.1)


def test_determinism():
    field = load("field_joke.json")
    a, _ = field_score.compose_bar(field, 3, 1_000_000)
    b, _ = field_score.compose_bar(field, 3, 1_000_000)
    assert a == b


def test_missing_dials_use_neutral_defaults():
    events, _, _ = compose_sequence([{}], n=2)  # no warmth, no dials at all
    assert_schema(events)
    assert events  # a silent room still ticks, softly


# -- end to end: HTTP field → spool dir, as demo.sh will run it ----------- #

class _FieldHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/field":
            body = (FIXTURES / "field_warm.json").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


def test_end_to_end_http_to_spool(tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FieldHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO / "field_score.py"),
             "--field", f"http://127.0.0.1:{port}/field",
             "--spool", str(tmp_path), "--bars", "4", "--interval", "0.02"],
            timeout=30, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    finally:
        server.shutdown()

    files = sorted(tmp_path.glob("*.jsonl"))
    assert [f.name for f in files] == [f"bar_{i:08d}.jsonl" for i in range(4)]
    for path in files:
        for ln in path.read_text().splitlines():
            assert set(json.loads(ln)) == KEYS


def test_field_down_scores_nothing_but_lives(tmp_path):
    """Cold room ≠ dead scorer: endpoint down → skip bars, keep running."""
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "field_score.py"),
         "--field", "http://127.0.0.1:1/field",  # nothing listens here
         "--spool", str(tmp_path), "--bars", "1", "--interval", "0.05"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(1.0)
        assert proc.poll() is None, "scorer must survive a dead field endpoint"
        assert list(tmp_path.glob("*.jsonl")) == []
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_cli_stdout_matches_spool(tmp_path):
    field_file = str(FIXTURES / "field_joke.json")
    out = subprocess.run(
        [sys.executable, str(REPO / "field_score.py"), "--field-file", field_file,
         "--stdout", "--bars", "1"],
        timeout=30, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert lines
    assert all(set(json.loads(ln)) == KEYS for ln in lines)
