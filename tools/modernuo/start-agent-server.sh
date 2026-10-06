#!/bin/bash
# Starts the ModernUO agent test server in the foreground, logging to stdout.
#
#   ./start-agent-server.sh            start with the existing world save
#   ./start-agent-server.sh --build    rebuild (dotnet publish, osx-arm64) first
#   ./start-agent-server.sh --fresh    delete Distribution/Saves, Backups and Archives first
#                                      (the admin account is re-seeded from modernuo.json on boot)
#
# Stop: Ctrl+C, or `pkill -INT -f "dotnet ModernUO.dll"` / `pkill -f "dotnet ModernUO.dll"` (SIGTERM)
# from elsewhere. If it was started with `&` from a non-interactive shell, SIGINT is ignored, so
# use SIGTERM. Neither signal saves. Autosave runs every 5 minutes; to save first, type `save` at the
# console (interactive terminal only) or `[Save` in game as admin. With a terminal attached,
# `shutdown` at the console also stops it.
set -euo pipefail

REPO_ROOT="${MODERNUO_DIR:-$(cd "$(dirname "$0")" && pwd)}"
DIST="$REPO_ROOT/Distribution"
DATA_DIR="${UO_DATA_DIR:-$HOME/Workspace/UOClassic}"
export PATH="/usr/local/share/dotnet:$PATH"

build=0
fresh=0
for arg in "$@"; do
    case "$arg" in
        --build) build=1 ;;
        --fresh) fresh=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

for f in map0LegacyMUL.uop tiledata.mul MultiCollection.uop client.exe; do
    if [ ! -f "$DATA_DIR/$f" ]; then
        echo "Missing UO data file: $DATA_DIR/$f" >&2
        exit 1
    fi
done

if lsof -nP -iTCP:2593 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port 2593 is already in use (is the server already running?)" >&2
    exit 1
fi

if [ "$build" = 1 ]; then
    (cd "$REPO_ROOT" && dotnet publish Projects/Application/Application.csproj -c Release -r osx-arm64 --self-contained=false)
fi

if [ "$fresh" = 1 ]; then
    rm -rf "$DIST/Saves" "$DIST/Backups" "$DIST/Archives"
    echo "World save wiped."
fi

cd "$DIST"
exec dotnet ModernUO.dll
