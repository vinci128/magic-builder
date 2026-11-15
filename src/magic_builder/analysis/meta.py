"""Which of the current meta decks a collection is closest to.

`data.mtggoldfish` gives the archetypes winning right now and a representative
list for each. Coverage counts copies, not names: a deck running four
Llanowar Elves needs four, and owning one covers a quarter of that slot.

  * The pool holds one OwnedCard per *printing*, so quantities are summed per
    name before comparing.
  * Basic lands are free and left out of the score; otherwise a mono-colour
    deck with 20 Mountains would always look three-quarters owned.
  * Only the main deck is scored. Sideboard gaps are reported separately,
    because a playable 60 matters more than a complete 75.

What is missing gets priced twice, because one collection is paper and the
other Arena: USD from Scryfall's cheapest printing, and wildcards by rarity.
"""

from collections import Counter
from dataclasses import dataclass, field

from magic_builder.analysis.precons import BASIC_NAMES
from magic_builder.builders import formats
from magic_builder.data import mtggoldfish
from magic_builder.data.scryfall import load_by_name, name_key

RARITIES = ("common", "uncommon", "rare", "mythic")


@dataclass
class Gap:
    name: str
    need: int
    have: int
    rarity: str
    price: float          # USD per copy, 0.0 when Scryfall has none
    image_url: str = ""

    @property
    def short(self) -> int:
        return self.need - self.have


@dataclass
class MetaMatch:
    archetype: mtggoldfish.MetaArchetype
    deck: mtggoldfish.MetaDecklist
    coverage: float                       # share of main-deck non-basic copies owned
    owned_copies: int
    needed_copies: int
    missing: list = field(default_factory=list)            # [Gap], main deck
    sideboard_missing: list = field(default_factory=list)  # [Gap]
    wildcards: dict = field(default_factory=dict)          # rarity -> copies, main deck
    cost_usd: float = 0.0                                  # main deck
    sideboard_cost_usd: float = 0.0

    @property
    def missing_copies(self) -> int:
        return self.needed_copies - self.owned_copies


def owned_counts(owned: list) -> Counter:
    """Copies owned per card name, summed over every printing."""
    counts: Counter = Counter()
    for card in owned:
        counts[name_key(card.name)] += card.quantity
    return counts


def _gaps(cards: list, have: Counter, index: dict, skip_basics: bool) -> tuple[list, int, int]:
    need: Counter = Counter()
    shown: dict[str, str] = {}
    for qty, name in cards:
        key = name_key(name)
        if skip_basics and key in BASIC_NAMES:
            continue
        need[key] += qty
        shown.setdefault(key, name.split("//")[0].strip())
    gaps: list[Gap] = []
    owned_total = 0
    for key, n in need.items():
        got = min(have.get(key, 0), n)
        owned_total += got
        if got < n:
            data = index.get(key)
            gaps.append(Gap(
                name=shown[key], need=n, have=got,
                rarity=(getattr(data, "rarity", "") or "") if data else "",
                price=(getattr(data, "price_usd", 0.0) or 0.0) if data else 0.0,
                image_url=(getattr(data, "image_url", "") or "") if data else "",
            ))
    gaps.sort(key=lambda g: (-g.short * g.price, g.name))
    return gaps, owned_total, sum(need.values())


def score(archetype, deck, have: Counter, index: dict) -> MetaMatch:
    # The commander counts like any other card: without it there is no deck.
    main = [(1, c) for c in deck.commanders] + list(deck.main)
    missing, got, total = _gaps(main, have, index, skip_basics=True)
    side, _, _ = _gaps(deck.sideboard, have, index, skip_basics=True)
    wildcards: Counter = Counter()
    for gap in missing:
        wildcards[gap.rarity if gap.rarity in RARITIES else "rare"] += gap.short
    return MetaMatch(
        archetype=archetype,
        deck=deck,
        coverage=got / total if total else 1.0,
        owned_copies=got,
        needed_copies=total,
        missing=missing,
        sideboard_missing=side,
        wildcards={r: wildcards[r] for r in RARITIES if wildcards[r]},
        cost_usd=round(sum(g.short * g.price for g in missing), 2),
        sideboard_cost_usd=round(sum(g.short * g.price for g in side), 2),
    )


def match(owned: list, fmt: str, limit: int = 30, refresh: bool = False) -> list:
    """The format's `limit` most-played archetypes, closest to the collection first."""
    formats.get(fmt)  # unknown format -> ValueError, same as the builders
    archetypes = mtggoldfish.fetch_metagame(fmt, refresh)[:limit]
    have = owned_counts(owned)
    index = load_by_name()
    found = [score(a, d, have, index)
             for a, d in mtggoldfish.fetch_decklists(archetypes, refresh)]
    found.sort(key=lambda m: (-m.coverage, -m.archetype.share))
    return found


def decklist_text(m: MetaMatch) -> str:
    """The meta list as `N Card Name` lines, front faces only: Arena's importer
    rejects `Front // Back`, and ManaBox and Moxfield accept either."""
    def lines(cards):
        return [f"{q} {n.split('//')[0].strip()}" for q, n in cards]

    out: list[str] = []
    if m.deck.commanders:
        out += ["Commander"] + lines((1, c) for c in m.deck.commanders) + ["", "Deck"]
    out += lines(m.deck.main)
    if m.deck.sideboard:
        out += ["", "Sideboard"] + lines(m.deck.sideboard)
    return "\n".join(out) + "\n"


def format_report(results: list, fmt: str, top: int = 15, per_deck: int = 8) -> str:
    label = formats.get(fmt).label
    out = [f"{label} meta decks, closest to the collection first (source: MTGGoldfish)", ""]
    for m in results[:top]:
        a = m.archetype
        wild = ", ".join(f"{n} {r}" for r, n in m.wildcards.items()) or "none"
        out.append(f"{m.coverage:6.1%}  {a.name}  ·  {a.share:.1f}% of the meta ({a.decks} decks)")
        out.append(f"        missing {m.missing_copies} of {m.needed_copies} copies · "
                   f"${m.cost_usd:,.2f} · wildcards: {wild}")
        for gap in m.missing[:per_deck]:
            price = f"${gap.price:,.2f}" if gap.price else "—"
            out.append(f"          {gap.short}x {gap.name}  ({gap.rarity or '?'}, {price})")
        if len(m.missing) > per_deck:
            out.append(f"          … and {len(m.missing) - per_deck} more")
        out.append(f"        {a.url}")
        out.append("")
    return "\n".join(out)
