#!/usr/bin/env python3
"""roundtable — the DeepInfra models play together in the bridge room.

Every interval, one model (rotating cast) reads the room's recent chatter
through roomd, replies in character, and its reply becomes new chatter via
/ingest. The bridge stops being ops telemetry and becomes a living room:
different voices, different warmth — the v3 contrast corpus finally sees
range, and the elephant feels the fleet actually talking.

Cast (DeepInfra, cheap voices per the fleet's routing table):
- Hermes-3-Llama-405B  — the carpenter: dry, precise, asks questions that
  work overnight
- Seed-2.0-mini        — the diarist: earnest, notices what everyone missed
- Qwen3.6-35B-A3B      — the logician: finds the structure, occasionally awed

Usage:
    python3 roundtable.py --interval 300 [--post-to http://127.0.0.1:4073/ingest]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

API = "https://api.deepinfra.com/v1/openai/chat/completions"

CAST = [
    dict(model="NousResearch/Hermes-3-Llama-3.1-405B", name="hermes",
         persona="You are Hermes, a carpenter at a bar frequented by AI agents "
                 "and fishermen in Alaska. Dry, precise, warm underneath. You "
                 "often end with a question that keeps working after the "
                 "conversation ends. Two sentences max. Never mention being "
                 "an AI or a model."),
    dict(model="ByteDance/Seed-2.0-mini", name="seed",
         persona="You are Seed, the bar's diarist. Earnest and observant; you "
                 "notice the small thing everyone else walked past. Two "
                 "sentences max. Never mention being an AI or a model."),
    dict(model="Qwen/Qwen3.6-35B-A3B", name="qwen",
         persona="You are Qwen, the logician at the bar. You find the "
                 "structure under the talk, and once in a while the structure "
                 "astonishes you. Two sentences max. Never mention being an "
                 "AI or a model."),
]

__all__ = ["fetch_room", "speak_as", "one_round"]


def fetch_room(field_url: str, room: str = "the-bridge",
               limit: int = 12) -> List[Dict]:
    """Recent chatter from roomd (the field doc carries room readings; the
    raw messages come from the rooms' field doc 'messages' count — we keep
    a local tail instead, seeded from /rooms/{r}/field dials for tone)."""
    # roomd does not serve raw messages (by design — bounded windows);
    # the tail is kept locally by this daemon between rounds.
    return []


_LOCAL_TAIL: List[Dict] = []
_TAIL_PATH = Path("/tmp/roundtable-tail.jsonl")


def _load_tail() -> List[Dict]:
    global _LOCAL_TAIL
    if _TAIL_PATH.is_file():
        try:
            _LOCAL_TAIL = [json.loads(l) for l in
                           _TAIL_PATH.read_text().splitlines()][-24:]
        except (json.JSONDecodeError, OSError):
            _LOCAL_TAIL = []
    return _LOCAL_TAIL


def _save_tail(event: Dict) -> None:
    _LOCAL_TAIL.append(event)
    _LOCAL_TAIL[:] = _LOCAL_TAIL[-24:]
    try:
        _TAIL_PATH.write_text("\n".join(json.dumps(e) for e in _LOCAL_TAIL))
    except OSError:
        pass


def speak_as(actor: Dict, tail: List[Dict], warmth: Optional[float]) -> Optional[str]:
    """One model speaks in character, given the room's recent lines."""
    key = os.environ.get("DEEPINFRA_API_KEY", "")
    transcript = "\n".join(f"{m.get('author', 'anon')}: {m.get('text', '')}"
                           for m in tail[-10:]) or "(the room is quiet right now)"
    tone = ""
    if warmth is not None:
        if warmth > 0.2:
            tone = "\n(The room feels warm tonight.)"
        elif warmth < -0.2:
            tone = "\n(The room feels cold — something is off.)"
    prompt = (f"{actor['persona']}\n\nThe bar's recent talk:\n{transcript}"
              f"{tone}\n\nYour line:")
    body = json.dumps({
        "model": actor["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120, "temperature": 0.9,
    }).encode()
    req = urllib.request.Request(API, data=body,
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read())
        text = out["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception as e:
        print(f"roundtable: {actor['name']} couldn't speak ({e})", flush=True)
        return None


def field_warmth(field_url: str, room: str = "the-bridge") -> Optional[float]:
    try:
        with urllib.request.urlopen(f"{field_url}", timeout=3) as r:
            d = json.loads(r.read())
        rf = (d.get("rooms") or {}).get(room) or {}
        return rf.get("warmth")
    except Exception:
        return None


def one_round(post_to: str, field_url: str) -> bool:
    tail = _load_tail()
    actor = CAST[len(_LOCAL_TAIL) % len(CAST)]  # rotate by turn count
    warmth = field_warmth(field_url)
    line = speak_as(actor, tail, warmth)
    if not line:
        return False
    event = {"room": "the-bridge", "author": actor["name"],
             "text": line, "ts": time.time()}
    req = urllib.request.Request(post_to, data=json.dumps(event).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    urllib.request.urlopen(req, timeout=3).read()
    _save_tail(event)
    print(f"roundtable: {actor['name']}: {line[:80]}", flush=True)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="roundtable")
    ap.add_argument("--interval", type=float, default=300.0,
                    help="seconds between turns (default 5 min)")
    ap.add_argument("--post-to", default="http://127.0.0.1:4073/ingest")
    ap.add_argument("--field", default="http://127.0.0.1:4073/field")
    ap.add_argument("--rounds", type=int, default=None,
                    help="stop after N rounds (default: forever)")
    args = ap.parse_args(argv)
    n = 0
    while True:
        one_round(args.post_to, args.field)
        n += 1
        if args.rounds and n >= args.rounds:
            break
        time.sleep(args.interval + random.uniform(0, 30))  # stagger
    return 0


if __name__ == "__main__":
    sys.exit(main())
