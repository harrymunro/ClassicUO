#!/bin/bash
# The planner (system two) on several models, one soak run each, one after another on the same
# client and server (cuo-m70.3): each prepared the same way (soak_prep.py runes), the same goal, the
# same packs sent at it (soak_packs.py). Run from brain/.
# Usage: scripts/planner_compare.sh TAG HOURS MODEL... ; writes logs/TAG-<n>.jsonl, one per model.
# SOAK_PORT is the client's agent port (5580 by default, the soak server's client).
TAG=$1; HOURS=$2; shift 2
GOAL="Hunt the undead at the Britain graveyard, starting from the West Britain bank. Keep yourself supplied: bandages for a warrior, reagents for a mage. Bank your gold and loot when the bag gets heavy."
cd "$(dirname "$0")/.."
n=0
for MODEL in "$@"; do
  n=$((n + 1))
  uv run python scripts/soak_prep.py runes
  echo "$MODEL" > "logs/$TAG-$n.model"
  PLANNER_MODEL="$MODEL" scripts/soak_run.sh "$TAG-$n" "$HOURS" yes "$GOAL"
  uv run uo-brain soak-report "logs/$TAG-$n.jsonl" --disruptions "logs/$TAG-$n.disruptions.jsonl" > "logs/$TAG-$n.report.md"
done
