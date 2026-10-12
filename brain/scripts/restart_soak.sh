#!/bin/bash
# A long soak with the brain killed and started again in the middle (cuo-6w8.7): `uo-brain session`
# on the client at $SOAK_PORT (5580 by default) for HOURS, killed (SIGKILL, no goodbye) after half of
# them, then started again with the same goal for the rest, both halves appending to logs/NAME.jsonl.
# The packs (soak_packs.py) come in the first half as in soak_run.sh. Run from brain/ after
# `uv run python scripts/soak_prep.py runes`.
# Usage: [SESSION_FLAGS="..."] scripts/restart_soak.sh NAME HOURS "GOAL"
NAME=$1; HOURS=$2; GOAL=$3
PORT=${SOAK_PORT:-5580}
cd "$(dirname "$0")/.."
HALF=$(python3 -c "print($HOURS / 2)")
HALF_S=$(python3 -c "print(int($HOURS * 1800))")
# The first half starts afresh (no state left by an earlier run); the second must not, so it resumes.
uv run uo-brain --port "$PORT" session "$GOAL" --hours "$HOURS" --log "logs/$NAME.jsonl" --fresh $SESSION_FLAGS > "logs/$NAME.out" 2>&1 &
SESSION=$!
for i in $(seq 1 30); do [ -s "logs/$NAME.jsonl" ] && break; sleep 1; done
T0=$(head -1 "logs/$NAME.jsonl" | python3 -c "import json,sys;print(json.loads(sys.stdin.readline())['t'])")
uv run python scripts/soak_packs.py "logs/$NAME.disruptions.jsonl" "logs/$NAME.jsonl" "$T0" > "logs/$NAME.disrupt.out" 2>&1 &
PACKER=$!
sleep "$HALF_S"
# The brain dies mid-goal, as a crash would leave it: no summary, no fold, nothing closed. uv's child
# python is the brain; kill it and uv.
pkill -9 -P "$SESSION" 2>/dev/null
kill -9 "$SESSION" 2>/dev/null
echo "{\"type\": \"restart\", \"t\": $(date +%s), \"note\": \"brain killed by restart_soak.sh\"}" >> "logs/$NAME.jsonl"
sleep 5
uv run uo-brain --port "$PORT" session "$GOAL" --hours "$HALF" --log "logs/$NAME.jsonl" $SESSION_FLAGS >> "logs/$NAME.out" 2>&1
kill "$PACKER" 2>/dev/null
echo "done $NAME"
