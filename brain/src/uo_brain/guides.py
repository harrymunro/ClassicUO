"""Turn guide and wiki pages into notes in the world store.

Guides (UOGuide, Stratics, a shard's own wiki) know things the server data doesn't say
outright: what a creature is weak to, which spots are busy, where player killers wait,
how a shard's rules differ. The planner model reads a page and writes short notes, one
fact each, tagged with the area it applies to and linked back to the page (source
"guide:<url>"). Re-importing a page replaces its notes.

The model's own knowledge can fill gaps as well. Those notes are stored as
"model:unverified" and tagged "unverified", so nothing treats them as fact until the
game confirms them.
"""

import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from . import costs, llm
from .world import World

MAX_BYTES = 2_000_000  # biggest page we download
MAX_CHARS = 60_000  # most page text we show the model (about 15k tokens)
MAX_NOTES = 60
USER_AGENT = "uo-brain/0.1 (guide import for a personal Ultima Online agent)"

ADD_NOTES = {"type": "function", "function": {
    "name": "add_notes",
    "description": "Store facts for the agent's world knowledge. One fact per note.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["notes"], "properties": {
        "notes": {"type": "array", "maxItems": MAX_NOTES, "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "area", "tags"], "properties": {
                "text": {"type": "string", "description": "One self-contained fact in one or two short sentences. "
                                                          "Name the place or creature; include coordinates if given."},
                "area": {"type": ["string", "null"], "description": "The town, dungeon or region it applies to, "
                                                                    "e.g. \"Britain\", \"Covetous\". Null if general."},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "One to four lower-case words: spawn, creature, vendor, travel, danger, "
                                        "loot, skill, rule, location, tactic."}}}}}}}}

GUIDE_PROMPT = """You turn Ultima Online guide and wiki pages into notes for an AI that plays the game. \
The notes go into a store the AI searches when it plans where to go and what to fight, so each note must make \
sense on its own.

Keep facts that help a player act: where things are (with coordinates when the page gives them), what spawns \
where and how dangerous it is, creature strengths, weaknesses and special attacks, vendors and services, \
travel (moongates, teleporters, routes, dungeon levels), dangers (guard zones, traps, places where player \
killers wait), useful loot and shard rules. Leave out lore, history, quest dialogue, patch-by-patch trivia and \
anything that is only an opinion.

Rules:
- One fact per note, at most two short sentences.
- Say which era or ruleset a fact belongs to when the page does (for example pre-AOS or Trammel only).
- Never include names of real players, guilds or staff.
- Set the area to the town, dungeon or region the fact applies to; use null for general facts.
- Record the facts by calling add_notes once."""

GAPS_PROMPT = """You are helping an AI that plays Ultima Online on classic Britannia (Felucca rules: guards \
in towns, player killing allowed outside them). Write what you know about one area from your own knowledge: \
where it is, how to get there, notable places and their rough coordinates, what spawns there and how dangerous \
it is for new, moderate and strong characters, and dangers. These notes are stored as unverified guesses until \
the game confirms them, so only write what you are fairly sure of, and say when a detail varies between eras \
or shards. One fact per note, at most two short sentences each, never names of real players. \
Record the facts by calling add_notes once."""


class _Text(HTMLParser):
    """Visible text of a page, one line per block, without scripts, menus and footers."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "aside", "button", "select"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article",
             "dd", "dt", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def page_text(html: str) -> tuple[str, str]:
    """(title, text) of an HTML page."""
    p = _Text()
    p.feed(html)
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip(" |") for line in "".join(p.parts).split("\n"))
    text = "\n".join(line for line in lines if line)
    return " ".join(p.title.split()), text


def fetch(url: str, timeout: float = 30.0) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_BYTES + 1)[:MAX_BYTES]
        charset = r.headers.get_content_charset() or "utf-8"
        kind = r.headers.get_content_type()
    body = raw.decode(charset, errors="replace")
    return page_text(body) if "html" in kind or body.lstrip().startswith("<") else ("", body)


def read_source(where: str) -> tuple[str, str, str]:
    """(source tag, title, text) for a URL or a local file."""
    if re.match(r"https?://", where):
        title, text = fetch(where)
        return f"guide:{where}", title, text
    path = Path(where).expanduser().resolve()
    body = path.read_text(errors="replace")[:MAX_BYTES]
    title, text = page_text(body) if path.suffix.lower() in (".html", ".htm") else (path.stem, body)
    return f"guide:{path.as_uri()}", title or path.stem, text


def _notes_from(result: llm.ChatResult) -> list[dict[str, Any]]:
    """The notes from the add_notes call, or from JSON in the reply if the model wrote
    them out instead (it can't be forced to call the tool on every model)."""
    found: list[Any] = []
    for call in result.tool_calls:
        if call.name == "add_notes":
            found += call.arguments.get("notes", [])
    if not result.tool_calls and result.content and "{" in result.content:
        try:
            data = json.loads(result.content[result.content.index("{"): result.content.rindex("}") + 1])
            found = data.get("notes", []) if isinstance(data, dict) else []
        except ValueError:
            pass
    return [n for n in found if isinstance(n, dict) and n.get("text")][:MAX_NOTES]


async def summarise(text: str, title: str, source: str, area: str | None = None, chat_fn: llm.ChatFn = llm.chat,
                    model: str | None = None) -> tuple[list[dict[str, Any]], llm.LlmUsage]:
    """Notes from one page's text: [{text, area, tags}], and what the call used."""
    clipped = text[:MAX_CHARS]
    intro = f"Page: {title or '(untitled)'}\nSource: {source.removeprefix('guide:')}\n"
    if area:
        intro += f"The page is about {area}; use that as the area when a fact names nothing more specific.\n"
    if len(text) > MAX_CHARS:
        intro += "The page was cut short.\n"
    with costs.kind("guide"):
        result = await chat_fn([{"role": "system", "content": GUIDE_PROMPT},
                                {"role": "user", "content": f"{intro}\n---\n{clipped}"}],
                               tools=[ADD_NOTES], tool_choice="add_notes", model=model, max_tokens=8192)
    notes = _notes_from(result)
    for n in notes:
        n["area"] = _area(n.get("area")) or area or None
    return notes, result.usage


def _area(value: Any) -> str | None:
    if not isinstance(value, str) or value.strip().lower() in ("", "null", "none", "general"):
        return None
    return value.strip()


def _store(world: World, notes: list[dict[str, Any]], source: str, extra_tags: tuple[str, ...] = ()) -> list[dict]:
    stored = []
    for n in notes:
        tags = [t for t in n.get("tags") or [] if isinstance(t, str)] + list(extra_tags)
        nid = world.add_note(str(n["text"]), area=n.get("area"), tags=tags, source=source)
        stored.append({"id": nid, "area": n.get("area"), "text": " ".join(str(n["text"]).split()),
                       "tags": sorted({t.strip().lower() for t in tags if t.strip()})})
    return stored


async def import_guide(world: World, where: str, area: str | None = None, chat_fn: llm.ChatFn = llm.chat,
                       model: str | None = None) -> dict[str, Any]:
    """Read a page (URL or file), summarise it into notes and store them, replacing any
    earlier notes from the same page."""
    source, title, text = read_source(where)
    if not text.strip():
        raise ValueError(f"no text found at {where}")
    notes, usage = await summarise(text, title, source, area, chat_fn, model)
    if not notes:
        raise ValueError(f"the planner model wrote no notes for {where}; nothing was changed")
    world.delete_source("notes", source)
    stored = _store(world, notes, source)
    return {"source": source, "title": title, "chars": len(text), "notes": stored, "usage": usage.to_log()}


async def fill_gaps(world: World, area: str, chat_fn: llm.ChatFn = llm.chat,
                    model: str | None = None) -> dict[str, Any]:
    """The model's own knowledge of an area, stored as unverified. Replaces earlier
    unverified notes for the same area."""
    with costs.kind("guide"):
        result = await chat_fn([{"role": "system", "content": GAPS_PROMPT},
                                {"role": "user", "content": f"Area: {area}"}],
                               tools=[ADD_NOTES], tool_choice="add_notes", model=model, max_tokens=8192)
    notes = _notes_from(result)
    if not notes:
        raise ValueError(f"the planner model wrote no notes about {area}; nothing was changed")
    for n in notes:
        n["area"] = area
    world.delete_source("notes", "model:unverified", area=area)
    stored = _store(world, notes, "model:unverified", extra_tags=("unverified",))
    return {"source": "model:unverified", "area": area, "notes": stored, "usage": result.usage.to_log()}
