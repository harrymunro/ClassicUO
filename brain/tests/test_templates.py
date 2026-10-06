"""The strategy templates shipped with the client read as they are meant to.

The files live with the client (src/ClassicUO.Client/Agent/Templates), which serves
them over RPC; this checks each one compiles to the settings its name promises.
"""
import asyncio
from pathlib import Path

import pytest

from uo_brain import strategy as strategies
from uo_brain.judge import HeuristicJudge

TEMPLATES = Path(__file__).resolve().parents[2] / "src/ClassicUO.Client/Agent/Templates"

# name -> (allow_flee, target_priority, looting, opening_spell, main_spell); None = not checked
EXPECTED = {
    "relentless": (False, "weakest_first", "valuables", "", ""),
    "survivor": (True, "closest_first", "valuables", "", ""),
    "farmer": (True, "closest_first", "everything", "", ""),
    "champion": (True, "strongest_first", None, "", ""),
    "no-loot": (True, None, "nothing", "", ""),
    "nuker": (True, None, None, "Explosion", "Energy Bolt"),
    "mana-saver": (True, None, None, "Lightning", "Magic Arrow"),
    "flamestriker": (True, "strongest_first", None, "Flamestrike", "Lightning"),
}


def parse(path: Path) -> dict:
    """Same format the client reads: '# Title', 'for:' and 'summary:' lines, then the text."""
    out, text, header = {"name": path.stem, "for": "any", "summary": ""}, [], True
    for line in (raw.strip() for raw in path.read_text().splitlines()):
        if header and line.startswith("# "):
            out["title"] = line[2:]
        elif header and line.split(":")[0].lower() in ("for", "summary"):
            key, value = line.split(":", 1)
            out[key.lower()] = value.strip()
        elif line:
            header = False
            text.append(line)
    out["text"] = "\n".join(text)
    return out


def test_every_template_is_expected_and_complete():
    names = {p.stem for p in TEMPLATES.glob("*.md")}
    assert names == set(EXPECTED)
    for p in TEMPLATES.glob("*.md"):
        t = parse(p)
        assert t["title"] and t["summary"] and t["text"], p.name
        assert t["for"] in ("any", "warrior", "mage"), p.name


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_compiles_to_its_settings(name):
    knobs, _ = asyncio.run(strategies.compile_strategy(HeuristicJudge(), parse(TEMPLATES / f"{name}.md")["text"]))
    got = (knobs.allow_flee, knobs.target_priority, knobs.looting, knobs.opening_spell, knobs.main_spell)
    for want, have in zip(EXPECTED[name], got):
        if want is not None:
            assert have == want, (name, got)


def test_templates_combine():
    text = "\n".join(parse(TEMPLATES / f"{n}.md")["text"] for n in ("nuker", "no-loot"))
    knobs, _ = asyncio.run(strategies.compile_strategy(HeuristicJudge(), text))
    assert (knobs.opening_spell, knobs.looting) == ("Explosion", "nothing")
