# fleet-rooms — the runtime keel

The glue repo of the fleet build (plan §1.5 of
`../fleet-next-level-plan.md`): thin, stdlib-only organs that connect the
five mature repos into **one pipeline, one command**. Sibling of
`fleet-connections` (TS-side); this repo owns nothing but the flight.

**Do not edit the other repos from here** — elephant, terrain, cns-echo,
fleet-audio, eisenstein each have owners. This repo only reads their seams.

## The one pipeline (target state, phase 1)

```
rooms.mud ──► terrain_core :4072 ──► scene.json (the compile contract)

eisenstein hex_mud --json ──► map.json (adjacency truth, exact ints)
        │
        ▼
elephant roomd :4073  (NEW — the field truth-holder)
   DialBank over each hex room · map_temperature · deadband
   GET /field · GET /rooms/{name}/field
   ring → USCP packet → ~/.hermes/cns_inbox
        │                                   │
        ▼                                   ▼
terrain elephant_bridge --poll      cns-echo --watch
(polls :4073/field,                 (echoes the ring; EchoSpace reads
  POSTs deltas to :4072/field;      the bus stream; ITS ring →
  index.html applies light/         USCP STATUS_REPORT → outbox)
  weather/particles)                        │
                                            ▼
field_score.py (NEW, glue repo)  reads :4073/field → JSONL MIDI
events → spool dir → fleet-audio --spool --dials-endpoint :4073/field
   warmth→scale/velocity · presence→string pad · panic→drums+tempo
   joke_landing→guitar flourish · FeelPulse shapes gain live
        │
        ▼
   out.wav — the room, heard
```

Doctrine: one truth-holder per quantity (eisenstein→adjacency,
elephant→field, terrain_core→scene, the bus→transport, fleet-audio→sound).
Nobody polls their own shadow.

## field_score.py — the room's field, one bar at a time

Reads `GET /field` from elephant roomd (`:4073`), composes one bar per
`--interval`, writes one atomically-renamed `bar_NNNNNNNN.jsonl` file per
bar into a spool dir. fleet-audio's `--spool` reader picks files up sorted
by name (hence the zero-padding) and consumes (deletes) them.

```
python3 field_score.py --field http://127.0.0.1:4073/field --spool demo/spool
python3 field_score.py --field-file tests/fixtures/field_panic.json --stdout --bars 1
```

A dead field endpoint skips bars with a warning and keeps running —
a cold room is not a dead room.

### JSONL schema (confirmed against `fleet-audio/src/midi.rs` + `src/io/jsonl_spool.rs`)

One JSON object per line, exact keys, integers only:

```json
{"timestamp_us": 1875000, "channel": 9, "note": 36, "velocity": 103}
```

| key           | type | meaning                                          |
|---------------|------|--------------------------------------------------|
| `timestamp_us`| u64  | microseconds since renderer start (monotonic)    |
| `channel`     | u8   | 0–15; voice map below                            |
| `note`        | u8   | 0–127, 60 = middle C                             |
| `velocity`    | u8   | 0–127; **0 = note-off** (every note_on is paired)|

Channel → voice (`midi.rs VoiceType::from_channel`): `0` piano · `1` bass ·
`2` strings · `3` guitar · `9` drums.

### Mapping v0 (deliberate and simple — plan §1.4 item 2)

| field quantity | musical consequence                                        |
|----------------|-------------------------------------------------------------|
| `warmth`       | pentatonic scale-degree center + velocity floor (30–54)     |
| `dials.presence` | string-pad (ch 2) density: 1–8 notes per bar              |
| `dials.volume` | velocity (stacks on the warmth floor)                       |
| `dials.panic` ≥ 0.5 | drums (ch 9) hit rate 4→16, tempo ×1.15, minor pentatonic |
| `dials.joke_landing` > 0.5 | guitar (ch 3) five-note pluck flourish, last beat |

Missing dials fall back to the elephant's neutral defaults
(presence 0.5, the rest 0). Composition is a pure function of
(field, bar_index) — deterministic, testable, replaceable.

## Repo layout

```
field_score.py         the emitter (stdlib only)
demo/                  sandbox spool/inbox/outbox for demo.sh (phase 1.5, lands
                       when all five lanes are up)
deploy/                phase-2 unit files live here (fleet-doctor.service +timer)
tests/                 pytest: fixture field → 8 bars well-formed, per-mapping
STATUS.md              orchestration status board (stale-safe)
```

## Tests

```
python3 -m pytest tests/ -q
```

Covers: the 8-bar well-formed acceptance shape (schema-exact events,
monotonic ticks, paired note-offs, spool sort order), each of the five
mappings in isolation, determinism, neutral-default handling, and an
end-to-end HTTP→spool run plus dead-endpoint survival.

## Phase 2 (planned here)

`deploy/fleet-doctor.service` + `.timer` (5 min) per plan §2 table, and
`fleet-doctor.py` — the chain of command's morning muster.
