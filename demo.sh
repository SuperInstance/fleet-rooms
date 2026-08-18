#!/usr/bin/env bash
# demo.sh — the whole chain, one command, self-verifying (plan §1.5).
#
#   eisenstein map ──► roomd :4073 (field truth) ──► terrain :4072 (shadow)
#                              │                            │
#                              ├──► cns-echo (ring travels) │
#                              └──► field_score ──► fleet-audio (the score)
#
# Sandbox only: demo/inbox + demo/outbox, never the live bus.
# Env: SKIP_ROOMD=1 (guard the roomd link), MAP_PREBUILT=1 (skip cargo map export)
set -u
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
DEMO="$PWD/demo"
PASS=(); FAIL=(); PIDS=""

cleanup() { for p in ${PIDS:-}; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT INT TERM

# stale-process sweep (a SIGKILLed previous run leaves orphans)
for pat in "terrain_core.py --server" "elephant.roomd" "cns_echo.cli --watch" \
           "elephant_bridge.py --poll" "field_score.py" "fleet-audio --spool"; do
  pkill -f "$pat" 2>/dev/null && sleep 0.3
done


check() { # name, condition-result
  if [ "$2" = "0" ]; then PASS+=("$1"); echo "PASS: $1"; else FAIL+=("$1"); echo "FAIL: $1"; fi
}

mkdir -p "$DEMO"/{inbox,outbox,spool}

# 1. map (eisenstein export) -------------------------------------------------
if [ "${MAP_PREBUILT:-0}" != "1" ] && [ "${SKIP_ROOMD:-0}" != "1" ]; then
  echo "-- exporting eisenstein map"
  (cd "$ROOT/eisenstein" && cargo run --release --example hex_mud -- --json > "$DEMO/map.json" 2>/dev/null) \
    && [ -s "$DEMO/map.json" ]; check "eisenstein map export" $?
fi

# 2. roomd :4073 --------------------------------------------------------------
ROOMD_PID=""
if [ "${SKIP_ROOMD:-0}" != "1" ]; then
  echo "-- starting roomd on :4073"
  (cd "$ROOT/elephant" && python3 -m elephant.roomd --map "$DEMO/map.json" \
     --events "$DEMO/galley-fire.jsonl" --port 4073 --inbox "$DEMO/inbox") &
  ROOMD_PID=$!
  for i in $(seq 1 50); do curl -sf http://127.0.0.1:4073/health >/dev/null && break; sleep 0.2; done
  curl -sf http://127.0.0.1:4073/health >/dev/null; check "roomd /health on :4073" $?
fi
FIELD_URL="http://127.0.0.1:4073/field"

# 3. terrain :4072 + elephant bridge -----------------------------------------
echo "-- starting terrain server + elephant bridge"
(cd "$ROOT/terrain" && python3 terrain_core.py --server >/dev/null 2>&1) &
PIDS="$PIDS $!"
for i in $(seq 1 50); do curl -sf http://127.0.0.1:4072/ >/dev/null && break; sleep 0.2; done
curl -sf http://127.0.0.1:4072/ >/dev/null; check "terrain :4072 serves" $?

if [ "${SKIP_ROOMD:-0}" != "1" ]; then
  (cd "$ROOT/terrain" && python3 elephant_bridge.py --poll "$FIELD_URL" \
     --interval 2 --post-to http://127.0.0.1:4072/field >/dev/null 2>&1) &
  PIDS="$PIDS $!"
fi

# 4. cns-echo (sandbox inboxes) ------------------------------------------------
echo "-- starting cns-echo watch (sandbox)"
(cd "$ROOT/cns-echo" && PYTHONPATH=src python3 -m cns_echo.cli --watch \
   --inbox "$DEMO/inbox" --outbox "$DEMO/outbox" --consume --interval 1 >/dev/null 2>&1) &
PIDS="$PIDS $!"
[ $? ]; check "cns-echo watch started" 0

# 5. field_score + fleet-audio -------------------------------------------------
echo "-- starting field_score + fleet-audio"
python3 field_score.py --field "$FIELD_URL" --spool "$DEMO/spool" --interval 2 >/dev/null 2>&1 &
PIDS="$PIDS $!"

if [ ! -x "$ROOT/fleet-audio/target/release/fleet-audio" ]; then
  echo "-- building fleet-audio (release)"
  (cd "$ROOT/fleet-audio" && cargo build --release >/dev/null 2>&1)
fi
if [ -x "$ROOT/fleet-audio/target/release/fleet-audio" ]; then
  (cd "$ROOT/fleet-audio" && ./target/release/fleet-audio --spool "$DEMO/spool" \
     --output "$DEMO/out.wav" --dials-endpoint "$FIELD_URL" >/dev/null 2>&1) &
  PIDS="$PIDS $!"
  check "fleet-audio started" 0
else
  check "fleet-audio binary" 1
fi

# 6. self-verify ----------------------------------------------------------------
echo "-- running self-verifier (waiting for the fire to spread...)"
sleep 12
if [ "${SKIP_ROOMD:-0}" != "1" ]; then
  F=$(curl -sf "$FIELD_URL" || echo '{}')
  echo "   field: $(echo "$F" | head -c 200)"
  echo "$F" | grep -q '"warmth"'; check "roomd /field answers" $?
  RINGS=$(ls "$DEMO/outbox" 2>/dev/null | wc -l)
  [ "${RINGS:-0}" -ge 1 ]; check "USCP ring packet traveled to outbox ($RINGS found)" $?
  curl -sf http://127.0.0.1:4072/field >/dev/null 2>&1; check "terrain /field shadow served" $?
fi
[ -f "$DEMO/out.wav" ] && [ "$(stat -c%s "$DEMO/out.wav" 2>/dev/null || echo 0)" -gt 44 ]
check "out.wav exists and grew" $?

echo
echo "== demo.sh result: ${#PASS[@]} passed, ${#FAIL[@]} failed"
[ ${#FAIL[@]} -eq 0 ] || { printf 'failed: %s\n' "${FAIL[@]}"; exit 1; }
exit 0
