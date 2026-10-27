#!/usr/bin/env python3
"""Before the first session on UO Renaissance (cuo-49t): is this machine set up for it? Offline: it reads files and
connects to nothing.

    python3 -I tools/uor/preflight.py [--settings bin/osx-arm64/settings.uor.json] [--data DIR]

It checks, and says what to do about each that isn't so:

- the client profile (tools/uor/settings.uor.json, copied next to `cuo`): the shard's login server, no encryption,
  client 5.0.8.3, RunUO's line of sight, attended play on (agent_attended_minutes: with no input from the player for
  that long the agent does nothing, since the shard's rules forbid killing monsters unattended), no brain started by
  the client, and a data folder named;
- the data folder: the shard's own 5.0.8.3 files (tiledata.mul, map0.mul, statics0.mul and the rest), not the stock
  7.0.117.1 data from EA's patch servers (which has .uop files the 5.0.8.3 client never had, and won't work there);
- the client itself in bin/osx-arm64, and the shard's places in the brain's `uor` world store (cuo-49t.8).

Exit code 0 when everything is ready, 1 otherwise. The steps that need Harry (asking staff, an account, the shard's
files) are on the bead.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WANT = {"ip": "login.uorenaissance.com", "port": 2593, "encryption": 0, "clientversion": "5.0.8.3",
        "agent_sight_rule": "runuo", "agent_start_brain": False}
FILES = ["tiledata.mul", "map0.mul", "staidx0.mul", "statics0.mul", "art.mul", "artidx.mul", "gumpart.mul",
         "gumpidx.mul", "hues.mul", "radarcol.mul", "cliloc.enu", "multi.mul", "multi.idx"]
STOCK = ["map0LegacyMUL.uop", "artLegacyMUL.uop", "gumpartLegacyMUL.uop"]  # 7.x data has these; 5.0.8.3 never did


def find(folder: Path, name: str) -> Path | None:
    """A file in the data folder, whatever its case (the shard's patch and EA's installer spell them differently)."""
    want = name.lower()
    return next((p for p in folder.iterdir() if p.name.lower() == want), None) if folder.is_dir() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--settings", type=Path, default=REPO / "bin" / "osx-arm64" / "settings.uor.json",
                    help="the profile next to cuo (default bin/osx-arm64/settings.uor.json; the repo's copy if that's missing)")
    ap.add_argument("--data", type=Path, help="the shard's data folder (default: the profile's ultimaonlinedirectory)")
    args = ap.parse_args()
    checks: list[tuple[bool, str, str]] = []  # ok, what, what to do

    path = args.settings if args.settings.exists() else REPO / "tools" / "uor" / "settings.uor.json"
    try:
        s = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        print(f"no profile to read: {path}: {e}")
        return 1
    checks.append((path != REPO / "tools" / "uor" / "settings.uor.json", f"the profile is next to the client ({path})",
                   "cp -f tools/uor/settings.uor.json bin/osx-arm64/ and start it there: ./cuo -settings settings.uor.json"))
    for key, value in WANT.items():
        checks.append((s.get(key) == value, f"{key} is {value!r} (it is {s.get(key)!r})", f"set {key} to {value!r}"))
    attended = s.get("agent_attended_minutes") or 0
    checks.append((isinstance(attended, int) and 1 <= attended <= 10,
                   f"attended play: the agent stops after {attended} min without input" if attended else "attended play is off",
                   "set agent_attended_minutes to 3: the shard's rules forbid killing monsters unattended"))

    data = args.data or (Path(s["ultimaonlinedirectory"]).expanduser() if s.get("ultimaonlinedirectory") else None)
    if data is None:
        checks.append((False, "a data folder is named", "set ultimaonlinedirectory to the folder of the shard's own files"))
    else:
        checks.append((data.is_dir(), f"the data folder {data} is there", "get the shard's files (its launcher or "
                       "ClientPatchLatest.zip, from its website or its staff) into a folder of their own"))
        missing = [f for f in FILES if find(data, f) is None]
        checks.append((not missing, "the shard's files are in it" + (f" (missing {', '.join(missing)})" if missing else ""),
                       "the 5.0.8.3 data with the shard's patch over it"))
        stock = [f for f in STOCK if find(data, f) is not None]
        checks.append((not stock, "it isn't the stock 7.x data" + (f" (it has {', '.join(stock)})" if stock else ""),
                       "the 7.0.117.1 files from EA's patch servers won't do: use the shard's own"))

    cuo = REPO / "bin" / "osx-arm64" / "cuo"
    checks.append((cuo.exists(), f"the client is built ({cuo})",
                   "dotnet publish src/ClassicUO.Client/ClassicUO.Client.csproj -c Release -r osx-arm64 -o bin/osx-arm64"))
    store = REPO / "brain" / "worlds" / "uor" / "world.sqlite"
    places = 0
    if store.exists():
        with sqlite3.connect(f"file:{store}?mode=ro", uri=True) as db:
            places = db.execute("SELECT COUNT(*) FROM places WHERE stale = 0").fetchone()[0]
    checks.append((places > 0, f"the uor world store has {places} places",
                   "python3 -I tools/uor/uor_map_markers.py PAGE.html --out brain/worlds/uor/uor-map-page.map, then "
                   "uv run --project brain uo-brain world --shard uor import-markers brain/worlds/uor/uor-map-page.map "
                   "--maps Felucca"))

    for ok, what, todo in checks:
        print(f"{'ok  ' if ok else 'TODO'} {what}" + ("" if ok else f"\n       {todo}"))
    ready = all(ok for ok, _, _ in checks)
    print("\nready for an attended session in assist mode" if ready else
          "\nnot ready: see the TODO lines (asking the shard's staff and an account are Harry's, on cuo-49t)")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
