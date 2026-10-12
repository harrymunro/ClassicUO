"""The window layout for filming a fleet (cuo-gka, cuo-cod.4), and its undo.

A client filmed as it comes has a 600 by 480 game view with the character's paperdoll open over the
middle of it, which hides the characters and everything they say. This sets, for every character of a
fleet file: a large game view (1004 by 920 at scale 0.7 in a 1696 by 954 window), the agent panel
beside it, the paperdoll and every other saved gump closed, the light fixed (night would darken the
clip), sound off, and full frame rate while the window isn't in front. The files it changes are
copied first, and `restore` puts them back.

    python3 tools/video/layout.py film brain/fleets/party-inn.toml [--client bin/osx-arm64]
    python3 tools/video/film.py --ports 5661-5665 --what "..." --bead ID -- uv run --project brain uo-brain fleet brain/fleets/party-inn.toml --windowed
    python3 tools/video/layout.py restore [--client bin/osx-arm64]

Run both with no client of that folder running: a client writes its profile when it logs out. The
characters must have logged in once (their profile folder exists).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WINDOW = {"X": 1696, "Y": 954}
PROFILE = {"game_window_size": {"X": 1004, "Y": 920}, "game_window_position": {"X": 10, "Y": 10}, "default_scale": 0.7,
           "agent_gump_x": 1030, "agent_gump_y": 60, "use_custom_light_level": True, "light_level": 4,
           "enable_sound": False, "enable_music": False, "reduce_f_p_s_when_inactive": False,
           "window_client_bounds": dict(WINDOW)}
NO_GUMPS = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<gumps>\n</gumps>'
BACKUP = ".filming-layout-backup"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def profiles(client: Path, fleet: Path) -> list[Path]:
    """The profile folder of each of the fleet's characters (Data/Profiles/<account>/<server>/<character>)."""
    out = []
    for a in tomllib.loads(fleet.read_text()).get("agent") or []:
        found = sorted((client / "Data" / "Profiles" / str(a["account"])).glob(f"*/{a['name']}/profile.json"))
        if not found:
            print(f"no profile yet for {a['name']} ({a['account']}): it has to log in once first", file=sys.stderr)
        out += [f.parent for f in found]
    return out


def film(client: Path, fleet: Path) -> int:
    backup = client / BACKUP
    if backup.exists():
        print(f"{backup} exists: a filming layout is already set; run `restore` first", file=sys.stderr)
        return 1
    folders = profiles(client, fleet)
    files = [client / "settings.json"] + [d / f for d in folders for f in ("profile.json", "gumps.xml")]
    for f in files:
        if f.exists():
            (backup / f.relative_to(client)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, backup / f.relative_to(client))
    settings = read(client / "settings.json")
    settings["window_size"] = dict(WINDOW)
    (client / "settings.json").write_text(json.dumps(settings, indent=2))
    for d in folders:
        (d / "profile.json").write_text(json.dumps(read(d / "profile.json") | PROFILE, indent=2))
        (d / "gumps.xml").write_text(NO_GUMPS)
    print(f"filming layout set for {', '.join(d.name for d in folders)} in {client}")
    return 0


def restore(client: Path) -> int:
    backup = client / BACKUP
    if not backup.exists():
        print(f"nothing to restore in {client}", file=sys.stderr)
        return 1
    n = 0
    for b in backup.rglob("*"):
        if b.is_file():
            shutil.copy2(b, client / b.relative_to(backup))
            n += 1
    shutil.rmtree(backup)
    print(f"restored {n} files in {client}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("film", "restore"))
    ap.add_argument("fleet", nargs="?", type=Path, help="the fleet file whose characters are filmed (for `film`)")
    ap.add_argument("--client", type=Path, default=Path(os.environ.get("UO_CLIENT_DIR") or REPO / "bin" / "osx-arm64"),
                    help="the client folder the fleet starts its clients from (default $UO_CLIENT_DIR, else bin/osx-arm64)")
    args = ap.parse_args()
    if args.what == "film":
        if args.fleet is None:
            ap.error("film needs the fleet file")
        return film(args.client, args.fleet)
    return restore(args.client)


if __name__ == "__main__":
    sys.exit(main())
