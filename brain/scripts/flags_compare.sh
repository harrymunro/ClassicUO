#!/bin/bash
# The same soak run with different session options, one after another on the same client and server,
# prepared the same way (soak_prep.py runes), the same goal and the same packs (soak_packs.py), like
# planner_compare.sh but varying options instead of the model (cuo-d4j: --no-escalate against the default).
# Usage: scripts/flags_compare.sh TAG HOURS "FLAGS" ... ; "-" for no flags. Writes logs/TAG-<n>.jsonl.
# SOAK_PORT is the client's agent port (5580 by default, the soak server's client). Run from brain/.
TAG=$1; HOURS=$2; shift 2
GOAL="Hunt the undead at the Britain graveyard, starting from the West Britain bank. Keep yourself supplied: bandages for a warrior, reagents for a mage. Bank your gold and loot when the bag gets heavy."
cd "$(dirname "$0")/.."
n=0
for FLAGS in "$@"; do
  n=$((n + 1))
  [ "$FLAGS" = "-" ] && FLAGS=""
  uv run python scripts/soak_prep.py runes
  echo "${FLAGS:-(default)}" > "logs/$TAG-$n.flags"
  # --fresh: a run must not carry on from the previous run's saved planner state (heartbeat.py).
  SESSION_FLAGS="--fresh $FLAGS" scripts/soak_run.sh "$TAG-$n" "$HOURS" yes "$GOAL"
  uv run uo-brain soak-report "logs/$TAG-$n.jsonl" --disruptions "logs/$TAG-$n.disruptions.jsonl" > "logs/$TAG-$n.report.md"
done
