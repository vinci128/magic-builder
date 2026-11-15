"""The current tournament metagame, read from MTGGoldfish.

MTGGoldfish publishes, per format, the archetypes that are winning right now:
each one's share of the meta, how many decks that share is counted from, a
tabletop price, and a representative list. Comparing those lists against a
collection answers "which real deck am I closest to?"

There is no API. Two pages are read:

  * `/metagame/<format>/full` lists the archetypes, one `archetype-tile` each.
  * `/archetype/<slug>` holds the representative list, HTML-escaped inside a
    hidden `<input id="deck_input_deck">`. The `/deck/download/<id>` endpoint
    that would serve it as text sits behind a Cloudflare challenge, so the
    embedded copy is the one that can actually be read.

For the singleton formats the commander is the first line of the list and has
the archetype's name; it is returned separately.

Both are cached on disk: the metagame for two days (it moves weekly with the
tournament results), the lists for a week.
"""

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import requests

from magic_builder.paths import CACHE_ROOT

BASE_URL = "https://www.mtggoldfish.com"
METAGAME_URL = BASE_URL + "/metagame/{fmt}/full"
ARCHETYPE_URL = BASE_URL + "/archetype/{slug}"
CACHE_DIR = CACHE_ROOT / "mtggoldfish"
METAGAME_TTL = 2 * 86400
DECKLIST_TTL = 7 * 86400
FORMATS = ("standard", "pauper", "modern", "brawl", "commander")

_HEADERS = {"User-Agent": "magic_builder/1.0 (+https://github.com/vinci128/magic_builder)"}
_TIMEOUT = 20
_WORKERS = 3

_TILE_SPLIT = re.compile(r"<div class='archetype-tile' id='\d+'>")
_HREF = re.compile(r"href=['\"]/archetype/([^'\"#?]+)")
_TITLE = re.compile(r"<span class='deck-price-paper'>\s*<a[^>]*>([^<]+)</a>")
_SHARE = re.compile(
    r"META%\s*</div>\s*<div class='archetype-tile-statistic-value'>\s*([\d.]+)%"
    r"\s*<span[^>]*>\s*\((\d+)\)")
_PRICE = re.compile(
    r"deck-price-paper'>\s*<div class='archetype-tile-statistic-name'>\s*Tabletop\s*</div>"
    r"\s*<div class='archetype-tile-statistic-value'>\s*\$\s*([\d,]+)")
_PIP = re.compile(r"ms-([wubrg]) ms-cost")
_KEY_CARDS = re.compile(r"<ul>(.*?)</ul>", re.DOTALL)
_LI = re.compile(r"<li>([^<]+)</li>")
_DECK_INPUT = re.compile(r"id=\"deck_input_deck\" value=\"([^\"]*)\"")
_LINE = re.compile(r"^(\d+)\s+(.+?)\s*$")


@dataclass
class MetaArchetype:
    slug: str
    name: str
    fmt: str
    share: float          # percent of the metagame, e.g. 11.7
    decks: int            # how many decks that share was counted from
    paper_price: int | None
    colors: list = field(default_factory=list)
    key_cards: list = field(default_factory=list)

    @property
    def url(self) -> str:
        return ARCHETYPE_URL.format(slug=self.slug)


@dataclass
class MetaDecklist:
    main: list            # [(qty, name)]
    sideboard: list       # [(qty, name)]
    commanders: list      # names; empty outside the singleton formats


def _fresh(path, ttl: int) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl


def _get(url: str) -> str:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_metagame(page: str, fmt: str) -> list:
    """The archetype tiles on a metagame page, in the order the site ranks them."""
    found: list[MetaArchetype] = []
    seen: set[str] = set()
    for tile in _TILE_SPLIT.split(page)[1:]:
        href = _HREF.search(tile)
        title = _TITLE.search(tile)
        if not href or not title or href.group(1) in seen:
            continue
        seen.add(href.group(1))
        share = _SHARE.search(tile)
        price = _PRICE.search(tile)
        keys = _KEY_CARDS.search(tile)
        found.append(MetaArchetype(
            slug=href.group(1),
            name=html.unescape(title.group(1)).strip(),
            fmt=fmt,
            share=float(share.group(1)) if share else 0.0,
            decks=int(share.group(2)) if share else 0,
            paper_price=int(price.group(1).replace(",", "")) if price else None,
            colors=[c for c in "WUBRG" if c.lower() in _PIP.findall(tile)],
            key_cards=[html.unescape(k).strip() for k in _LI.findall(keys.group(1))] if keys else [],
        ))
    return found


# What a failed fetch raises: the network, a page that no longer parses, the cache.
FETCH_ERRORS = (requests.RequestException, RuntimeError, OSError)


def fetch_metagame(fmt: str, refresh: bool = False) -> list:
    """The format's current archetypes, most-played first."""
    if fmt not in FORMATS:
        raise ValueError(f"MTGGoldfish has no metagame for {fmt!r}. Known: {', '.join(FORMATS)}")
    path = CACHE_DIR / f"metagame-{fmt}.json"
    if not refresh and _fresh(path, METAGAME_TTL):
        try:
            return [MetaArchetype(**a) for a in json.loads(path.read_text(encoding="utf-8"))]
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    found = parse_metagame(_get(METAGAME_URL.format(fmt=fmt)), fmt)
    if not found:
        # A redesign or a challenge page; caching an empty meta would hide that for days.
        raise RuntimeError(f"No archetypes found on MTGGoldfish's {fmt} metagame page.")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(a) for a in found]), encoding="utf-8")
    return found


def parse_decklist(page: str, archetype_name: str = "", singleton: bool = False) -> MetaDecklist:
    m = _DECK_INPUT.search(page)
    if not m:
        raise RuntimeError("No decklist on that archetype page.")
    main: list = []
    side: list = []
    target = main
    for raw in html.unescape(m.group(1)).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower() == "sideboard":
            target = side
            continue
        hit = _LINE.match(line)
        if hit:
            target.append((int(hit.group(1)), hit.group(2)))
    commanders: list = []
    if singleton and main:
        # The commander leads the list and names the archetype; partners are
        # not marked apart, so only the lead card is taken.
        wanted = archetype_name.lower()
        idx = next((i for i, (_, n) in enumerate(main) if n.lower() == wanted), 0)
        commanders.append(main.pop(idx)[1])
    return MetaDecklist(main, side, commanders)


def fetch_decklist(archetype: MetaArchetype, refresh: bool = False) -> MetaDecklist:
    path = CACHE_DIR / "lists" / f"{archetype.slug}.json"
    if not refresh and _fresh(path, DECKLIST_TTL):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MetaDecklist([tuple(x) for x in data["main"]],
                                [tuple(x) for x in data["sideboard"]],
                                data["commanders"])
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            pass
    deck = parse_decklist(_get(archetype.url), archetype.name,
                          singleton=archetype.fmt in ("brawl", "commander"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(deck)), encoding="utf-8")
    return deck


def fetch_decklists(archetypes: list, refresh: bool = False) -> list:
    """(archetype, decklist) for each archetype whose list could be read.

    A few pages at a time, and one that fails is dropped rather than failing
    the lot.
    """
    def one(arch):
        try:
            return arch, fetch_decklist(arch, refresh)
        except FETCH_ERRORS:
            return arch, None

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        return [(a, d) for a, d in pool.map(one, archetypes) if d is not None]
