#!/usr/bin/env python3
"""UO Renaissance's own map page as a marker file the brain can import (cuo-49t.8).

The shard publishes no marker file for towns, moongates and dungeons (only house.txt, the
players' houses), but its map page (uorenaissance.com/map/) draws them from lines in its own
script:

    addMarkerWithPopUp(markers_moongates, icon_moongate, 1336, ZOOMIFY_HEIGHT - 1997, "Britain Moongate");

The two numbers are game coordinates (checked against the standard map's shrines: within two
tiles). This reads a saved copy of the page and writes a UOAM .map file (facet 1, UOAM's Felucca:
the shard has one facet), which `uo-brain world --shard uor import-markers FILE --maps Felucca`
turns into places. Lines the page has commented out are left out, and so are its houses.

    python3 -I tools/uor/uor_map_markers.py PAGE.html --out brain/worlds/uor/uor-map-page.map

The page is the shard's work: keep the copy and the file made from it out of the repo
(brain/worlds/ is gitignored). With the site down, the Wayback Machine has the page, e.g.
http://web.archive.org/web/20260412162235id_/http://www.uorenaissance.com/map/index.php
"""

import argparse
import html
import re
import sys
from pathlib import Path

# The page's layers -> a marker type the importer knows (markers.TYPE_KINDS), or one it keeps as a landmark.
LAYERS = {"cities": "city", "shrines": "shrine", "dungeons": "dungeon", "moongates": "moongate",
          "cemetaries": "cemetery", "pointsofinterest": "point of interest", "lore": "lore"}
LINE = re.compile(r'addMarkerWithPopUp\(\s*markers_(\w+)\s*,\s*\w+\s*,\s*(\d+)\s*,\s*ZOOMIFY_HEIGHT\s*-\s*(\d+)\s*,\s*"(.*?)"\s*\)')


def markers(page: str) -> tuple[list[tuple[str, int, int, str]], dict[str, int]]:
    """(type, x, y, name) for every marker the page draws, and how many lines were left out, by why."""
    out, skipped = [], {"commented out": 0, "houses": 0, "another layer": 0}
    for raw in page.splitlines():
        line = raw.strip()
        m = LINE.search(line)
        if not m:
            continue
        if line.startswith("//") or line.startswith("<!--"):
            skipped["commented out"] += 1
            continue
        layer, x, y, text = m[1], int(m[2]), int(m[3]), m[4]
        if layer == "houses":
            skipped["houses"] += 1
            continue
        if layer not in LAYERS:
            skipped["another layer"] += 1
            continue
        # "Shrine of Justice<br />(Mantra: Beh)" -> "Shrine of Justice (Mantra: Beh)"
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("\\'", "'").split())
        out.append((LAYERS[layer], x, y, name))
    return out, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page", type=Path, help="a saved copy of the map page")
    ap.add_argument("--out", type=Path, required=True, help="the UOAM .map file to write")
    args = ap.parse_args()
    found, skipped = markers(args.page.read_text(errors="replace"))
    if not found:
        sys.exit(f"no markers in {args.page}: is it the map page?")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        f.write("3\n")
        for kind, x, y, name in found:
            f.write(f"+{kind}: {x} {y} 1 {name}\n")
    by: dict[str, int] = {}
    for kind, *_ in found:
        by[kind] = by.get(kind, 0) + 1
    print(f"{len(found)} markers written to {args.out}: " + ", ".join(f"{n} {k}" for k, n in by.items())
          + "; left out: " + ", ".join(f"{n} {k}" for k, n in skipped.items() if n))


if __name__ == "__main__":
    main()
