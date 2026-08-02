"""Naming a built deck the way players talk about decks.

"Selesnya Counters" tells you more at a glance than "WG deck" — a colour name
(the guild/shard/wedge everyone already uses) plus the thing the deck is
actually doing. The theme comes from the builder's own synergy scoring, so the
name can never claim a plan the card choices didn't follow.
"""

# Keys are colour letters in WUBRG order.
COMBO_NAMES = {
    "": "Colourless",
    "C": "Colourless",
    "W": "Mono-White", "U": "Mono-Blue", "B": "Mono-Black",
    "R": "Mono-Red", "G": "Mono-Green",
    "WU": "Azorius", "WB": "Orzhov", "WR": "Boros", "WG": "Selesnya",
    "UB": "Dimir", "UR": "Izzet", "UG": "Simic",
    "BR": "Rakdos", "BG": "Golgari", "RG": "Gruul",
    "WUB": "Esper", "WUR": "Jeskai", "WUG": "Bant", "WBR": "Mardu",
    "WBG": "Abzan", "WRG": "Naya", "UBR": "Grixis", "UBG": "Sultai",
    "URG": "Temur", "BRG": "Jund",
    "WUBR": "Yore-Tiller", "WUBG": "Witch-Maw", "WURG": "Ink-Treader",
    "WBRG": "Dune-Brood", "UBRG": "Glint-Eye",
    "WUBRG": "Five-Colour",
}


def color_key(colors) -> str:
    """Canonical WUBRG-ordered string for a set/list/str of colour letters."""
    letters = set(colors or ())
    return "".join(c for c in "WUBRG" if c in letters)


def color_label(colors) -> str:
    key = color_key(colors)
    return COMBO_NAMES.get(key) or f"{len(key)}-Colour"


def shape_word(avg_cmc: float | None, interaction_share: float = 0.0) -> str:
    """Fallback theme for decks with no synergy theme worth naming.

    `interaction_share` is removal and counterspells as a fraction of the deck,
    so the threshold means the same thing at 60 cards and at 100.
    """
    if interaction_share >= 0.17:
        return "Control"
    if avg_cmc is not None:
        if avg_cmc <= 2.3:
            return "Aggro"
        if avg_cmc >= 3.4:
            return "Ramp"
    return "Midrange"


def deck_name(colors, theme: str | None = None, *,
              avg_cmc: float | None = None, interaction_share: float = 0.0) -> str:
    return f"{color_label(colors)} {theme or shape_word(avg_cmc, interaction_share)}"
