#!/usr/bin/env bash
# Save an OpenRouter API key to brain/.env (gitignored, mode 600) for uo-brain.
#
#   ./set-openrouter-key.sh            # prompts; the key is not echoed
#   pbpaste | ./set-openrouter-key.sh  # from the clipboard, keeps it out of shell history
#
# The key is checked against OpenRouter before it is saved. Set NO_VERIFY=1 to skip that.
set -euo pipefail

ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$0")" && pwd)/.env}"

if [ -t 0 ]; then
    read -r -s -p "OpenRouter API key: " key
    echo
else
    read -r key || true
fi
key="$(printf '%s' "$key" | tr -d '[:space:]')"

if [ -z "$key" ]; then
    echo "No key given." >&2
    exit 1
fi
case "$key" in
    sk-or-*) ;;
    *) echo "Warning: OpenRouter keys usually start with sk-or-." >&2 ;;
esac

if [ "${NO_VERIFY:-0}" != "1" ]; then
    status=$(curl -s -o /tmp/openrouter-key-check.$$ -w '%{http_code}' --max-time 15 \
        -H "Authorization: Bearer $key" https://openrouter.ai/api/v1/key || echo "000")
    case "$status" in
        200)
            python3 - /tmp/openrouter-key-check.$$ <<'EOF'
import json, sys
d = json.load(open(sys.argv[1])).get("data", {})
limit = d.get("limit_remaining", d.get("limit"))
print(f"Key OK: {d.get('label') or 'unlabelled'}, used ${d.get('usage', 0):.4f}"
      + (f", ${limit:.2f} remaining" if isinstance(limit, (int, float)) else ", no limit"))
EOF
            ;;
        401|403)
            rm -f /tmp/openrouter-key-check.$$
            echo "OpenRouter rejected the key (HTTP $status). Nothing saved." >&2
            exit 1
            ;;
        *)
            echo "Could not check the key (HTTP $status); saving it anyway." >&2
            ;;
    esac
    rm -f /tmp/openrouter-key-check.$$
fi

umask 077
touch "$ENV_FILE"
tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
grep -v '^OPENROUTER_API_KEY=' "$ENV_FILE" > "$tmp" || true
printf 'OPENROUTER_API_KEY=%s\n' "$key" >> "$tmp"
mv "$tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "Saved to $ENV_FILE"
echo "Try Jev without the game: uv run uo-brain replay logs/<some-run>.jsonl --judge jev --limit 5"
