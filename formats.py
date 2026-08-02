"""The deck formats this builder can target.

Everything format-specific lives here so the builders stay generic: which
Scryfall legality key gates the card pool, how many cards the deck holds, how
many copies of one card are allowed, and whether a sideboard is expected.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FormatSpec:
    key: str          # Scryfall legality key, e.g. card.legalities["pauper"]
    label: str
    singleton: bool   # one commander + 1-of everything, vs. constructed 4-ofs
    deck_size: int    # total cards including the commander
    max_copies: int
    sideboard: int    # 0 when the format has no sideboard here


FORMATS: dict[str, FormatSpec] = {
    "commander": FormatSpec("commander", "Commander", True, 100, 1, 0),
    # Arena's Historic Brawl: same shape as Commander, much smaller card pool.
    "brawl": FormatSpec("brawl", "Brawl", True, 100, 1, 0),
    "standard": FormatSpec("standard", "Standard", False, 60, 4, 15),
    # Scryfall's `pauper` legality is already common-only, so no rarity filter
    # of our own is needed.
    "pauper": FormatSpec("pauper", "Pauper", False, 60, 4, 15),
}

SINGLETON_FORMATS = [k for k, f in FORMATS.items() if f.singleton]
CONSTRUCTED_FORMATS = [k for k, f in FORMATS.items() if not f.singleton]


def get(name: str) -> FormatSpec:
    try:
        return FORMATS[name]
    except KeyError:
        raise ValueError(f"Unknown format {name!r}. Known: {', '.join(FORMATS)}")
