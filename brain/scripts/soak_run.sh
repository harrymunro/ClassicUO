#!/bin/bash
# A soak run: `uo-brain session` on the client at $SOAK_PORT (5580 by default) for HOURS, with the
# pack script beside it when PACKS is "yes". Run from brain/ after `uv run python scripts/soak_prep.py runes`.
# Usage: scripts/soak_run.sh NAME HOURS yes|no "GOAL"; writes logs/NAME.jsonl (and .disruptions.jsonl).
NAME=$1; HOURS=$2; PACKS=$3; GOAL=$4
PORT=${SOAK_PORT:-5580}
cd "$(dirname "$0")/.."
uv run uo-brain --port "$PORT" session "$GOAL" --hours "$HOURS" --log "logs/$NAME.jsonl" > "logs/$NAME.out" 2>&1 &
SESSION=$!
if [ "$PACKS" = yes ]; then
  for i in $(seq 1 30); do [ -s "logs/$NAME.jsonl" ] && break; sleep 1; done
  T0=$(head -1 "logs/$NAME.jsonl" | python3 -c "import json,sys;print(json.loads(sys.stdin.readline())['t'])")
  uv run python scripts/soak_packs.py "logs/$NAME.disruptions.jsonl" "logs/$NAME.jsonl" "$T0" > "logs/$NAME.disrupt.out" 2>&1 &
  PACKER=$!
fi
wait $SESSION
[ -n "$PACKER" ] && kill $PACKER 2>/dev/null
echo "done $NAME"
