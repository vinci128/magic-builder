"""60-card constructed deck builder (Standard and Pauper).

Unlike Commander, constructed decks want redundancy: up to 4 copies of a card,
a low curve, and a critical mass of interaction. The builder:

1. Merges printings by card name and caps playable copies at 4.
2. Filters to cards legal in the requested format.
3. Scores every card on rate (stats per mana, keywords, removal/draw value).
4. Picks the best mono-color or two-color identity by summed pool strength.
5. Boosts cards whose synergy tags pair up inside the chosen colors
   (lifegain sources ↔ lifegain payoffs, token makers ↔ token payoffs, ...).
6. Fills 36-38 non-land slots under per-CMC curve caps, then builds a mana
   base from owned dual lands plus basics weighted by mana-symbol demand.
7. Picks a 15-card sideboard out of what the main deck left behind, favouring
   the answers a maindeck can't afford to run (artifact/enchantment removal,
   graveyard hate, counterspells, sweepers).
"""

import re
from dataclasses import dataclass, field

import archetype
import formats
from collection import OwnedCard
from deck_builder import BASIC_LAND_NAMES, _is_basic_land, _make_basic

DECK_SIZE = 60
MAX_COPIES = 4
NONLAND_TARGET = 37          # 23 lands
MIN_INTERACTION = 8          # removal/counters we try to fit
CURVE_CAPS = {0: 4, 1: 12, 2: 14, 3: 12, 4: 8, 5: 5, 6: 3}  # max copies per CMC bucket (6 = 6+)


@dataclass
class DeckEntry:
    card: OwnedCard
    count: int

    @property
    def name(self) -> str:
        return self.card.name


# ── Pool preparation ─────────────────────────────────────────────────────────

def merge_by_name(owned: list, max_copies: int = MAX_COPIES) -> list[DeckEntry]:
    """Collapse printings into one entry per card name, capped at `max_copies`."""
    by_name: dict[str, DeckEntry] = {}
    for c in owned:
        entry = by_name.get(c.name)
        if entry:
            entry.count += c.quantity
            # Keep the printing with the richest metadata
            if not entry.card.type_line and c.type_line:
                entry.card = c
            # ...and, among equally complete ones, the cheapest: any printing plays the same.
            elif (c.type_line and c.price_usd
                  and (not entry.card.price_usd or c.price_usd < entry.card.price_usd)):
                entry.card = c
        else:
            by_name[c.name] = DeckEntry(card=c, count=c.quantity)
    for entry in by_name.values():
        if not _is_basic_land(entry.card):
            entry.count = min(entry.count, max_copies)
    return list(by_name.values())


def legal_pool(entries: list[DeckEntry], fmt: str) -> list[DeckEntry]:
    return [e for e in entries if e.card.legalities.get(fmt) == "legal"]


# ── Card evaluation ──────────────────────────────────────────────────────────

_GOOD_KEYWORDS = {
    "flying": 1.2, "lifelink": 1.0, "deathtouch": 0.8, "first strike": 0.8,
    "double strike": 1.8, "trample": 0.6, "haste": 0.8, "vigilance": 0.5,
    "menace": 0.6, "ward": 0.8, "flash": 0.6, "hexproof": 0.8, "prowess": 0.6,
}

_REMOVAL_PATTERNS = (
    "destroy target creature", "destroy target permanent", "destroy target nonland",
    "exile target creature", "exile target permanent", "exile target nonland",
    "deals damage to any target", "deals damage to target creature",
    "counter target spell", "counter target creature spell",
    "target creature gets -", "destroy all creatures",
)


def _is_interaction(card: OwnedCard) -> bool:
    t = card.oracle_text.lower()
    return any(p in t for p in _REMOVAL_PATTERNS)


def _tags(card: OwnedCard) -> set:
    """Synergy tags used to boost cards that work together."""
    t = card.oracle_text.lower()
    tags = set()
    if "you gain" in t and "life" in t or "lifelink" in [k.lower() for k in card.keywords]:
        tags.add("lifegain_source")
    if "whenever you gain life" in t:
        tags.add("lifegain_payoff")
    if "create" in t and "token" in t:
        tags.add("token_source")
    if "creatures you control get" in t or "each creature you control" in t:
        tags.add("token_payoff")
    if "+1/+1 counter" in t:
        tags.add("counters")
    if re.search(r"add \{[wubrgc]\}", t) or "adds one mana" in t:
        tags.add("mana_dork")
    return tags


def card_power(card: OwnedCard) -> float:
    """Format-agnostic rate score: how strong is this card on its own?"""
    if "Land" in card.type_line:
        return 0.0
    t = card.oracle_text.lower()
    kws = {k.lower() for k in card.keywords}
    cost = max(card.cmc, 1.0)
    score = 0.0

    if "Creature" in card.type_line:
        try:
            stats = float(card.power or 0) + float(card.toughness or 0)
        except ValueError:  # '*' power
            stats = card.cmc * 2
        score += (stats / cost) * 1.5
        score += sum(w for k, w in _GOOD_KEYWORDS.items() if k in kws)
        if "defender" in kws:
            score -= 1.5
        if "when this creature enters" in t or "when this enters" in t:
            score += 0.8  # ETB value
    if _is_interaction(card):
        score += 3.0 + max(0.0, 3.0 - cost)  # cheap interaction is premium
    if "draw" in t and "card" in t:
        score += 1.5
    if "you gain" in t and "life" in t:
        score += 0.3

    score += {"rare": 0.8, "mythic": 1.2}.get(card.rarity, 0.0)
    score -= max(0.0, card.cmc - 4) * 0.7  # expensive cards need to win the game
    return score


# ── Color selection ──────────────────────────────────────────────────────────

_COLOR_PAIRS = ["W", "U", "B", "R", "G",
                "WU", "WB", "WR", "WG", "UB", "UR", "UG", "BR", "BG", "RG"]


def _castable(card: OwnedCard, colors: set) -> bool:
    return set(card.color_identity).issubset(colors)


def _color_pool(entries: list[DeckEntry], colors: set) -> list[DeckEntry]:
    return [
        e for e in entries
        if "Land" not in e.card.type_line and _castable(e.card, colors) and e.count > 0
    ]


def _pool_strength(entries: list[DeckEntry], colors: set) -> float:
    """Sum of the best NONLAND_TARGET copy-scores (rate + synergy) in these colors."""
    pool = _color_pool(entries, colors)
    copy_scores: list[float] = []
    for e in pool:
        s = card_power(e.card) + synergy_boost(e.card, pool)
        copy_scores.extend([s] * e.count)
    copy_scores.sort(reverse=True)
    return sum(copy_scores[:NONLAND_TARGET])


def choose_colors(entries: list[DeckEntry]) -> set:
    best, best_score = {"W"}, float("-inf")
    for pair in _COLOR_PAIRS:
        colors = set(pair)
        score = _pool_strength(entries, colors)
        if len(colors) == 1:
            score *= 1.05  # mono-color consistency bonus
        if score > best_score:
            best, best_score = colors, score
    return best


# ── Synergy boost ────────────────────────────────────────────────────────────

_TAG_PAIRS = {
    "lifegain_payoff": "lifegain_source",
    "lifegain_source": "lifegain_payoff",
    "token_payoff": "token_source",
    "token_source": "token_payoff",
}


# Player-facing names for the tags above, for the web UI's synergy panel.
TAG_LABELS = {
    "lifegain_source": "Life gain",
    "lifegain_payoff": "Life-gain payoffs",
    "token_source": "Token makers",
    "token_payoff": "Team pumps",
    "counters": "+1/+1 counters",
    "mana_dork": "Mana ramp",
}

# The one-word version, for naming the deck ("Selesnya Counters").
TAG_ARCHETYPES = {
    "counters": "Counters",
    "token_source": "Tokens",
    "token_payoff": "Go-Wide",
    "lifegain_payoff": "Lifegain",
    "lifegain_source": "Lifegain",
    "mana_dork": "Ramp",
}


def synergy_tags(card: OwnedCard) -> set:
    """Public view of the tags the builder scores on."""
    return _tags(card)


def is_interaction(card: OwnedCard) -> bool:
    """Public view of the removal/counterspell test the builder reserves slots for."""
    return _is_interaction(card)


def deck_archetype(entries: list[DeckEntry], colors: set) -> str:
    """Name the deck after its colours and the theme its own cards carry.

    Falls back to a shape word (Aggro / Midrange / Control / Ramp) when no
    synergy tag has enough copies behind it to be called a theme.
    """
    tag_counts: dict[str, int] = {}
    nonland = weighted = interaction = 0
    for e in entries:
        if "Land" in e.card.type_line:
            continue
        nonland += e.count
        weighted += e.card.cmc * e.count
        if _is_interaction(e.card):
            interaction += e.count
        for tag in _tags(e.card):
            tag_counts[tag] = tag_counts.get(tag, 0) + e.count

    named = [(count, TAG_ARCHETYPES[tag]) for tag, count in tag_counts.items()
             if tag in TAG_ARCHETYPES]
    # A theme needs a real share of the deck behind it, or the name overclaims.
    theme = max(named)[1] if named and max(named)[0] >= 8 else None
    avg_cmc = (weighted / nonland) if nonland else None
    share = (interaction / nonland) if nonland else 0.0
    return archetype.deck_name(colors, theme, avg_cmc=avg_cmc, interaction_share=share)


def synergy_boost(card: OwnedCard, pool: list[DeckEntry]) -> float:
    tags = _tags(card)
    if not tags:
        return 0.0
    boost = 0.0
    for tag in tags:
        partner = _TAG_PAIRS.get(tag)
        if not partner:
            continue
        partners = sum(e.count for e in pool if partner in _tags(e.card) and e.card.name != card.name)
        boost += min(partners, 12) * 0.15
    return boost


# ── Mana base ────────────────────────────────────────────────────────────────

def _mana_symbols(deck: list[DeckEntry]) -> dict[str, int]:
    counts = {c: 0 for c in "WUBRG"}
    for e in deck:
        for sym in re.findall(r"([WUBRG])", e.card.mana_cost.upper()):
            counts[sym] += e.count
    return counts


def _build_mana_base(colors: set, nonlands: list[DeckEntry], entries: list[DeckEntry],
                     land_count: int) -> list[DeckEntry]:
    lands: list[DeckEntry] = []
    remaining = land_count

    # Owned nonbasic lands that only produce our colors (duals/utility)
    if len(colors) > 1:
        duals = [
            e for e in entries
            if "Land" in e.card.type_line and not _is_basic_land(e.card)
            and set(e.card.color_identity) and set(e.card.color_identity).issubset(colors)
        ]
        duals.sort(key=lambda e: -len(set(e.card.color_identity)))
        for e in duals:
            take = min(e.count, remaining, MAX_COPIES)
            if take > 0:
                lands.append(DeckEntry(card=e.card, count=take))
                remaining -= take
            if remaining <= 8:  # keep room for basics
                break

    symbols = _mana_symbols(nonlands)
    active = [c for c in "WUBRG" if c in colors]
    total = sum(symbols[c] for c in active) or len(active)
    allocated = 0
    for i, c in enumerate(active):
        n = remaining - allocated if i == len(active) - 1 else round(remaining * symbols[c] / total)
        if n > 0:
            lands.append(DeckEntry(card=_make_basic(c, 0), count=n))
        allocated += n
    return lands


# ── Sideboard ────────────────────────────────────────────────────────────────

# Answers that are dead against half the field, so they belong in the 15 rather
# than the 60. Weighted by how narrow — and therefore how sideboard-y — they are.
_HATE_PATTERNS = (
    ("destroy target artifact", 4.0),
    ("destroy target enchantment", 4.0),
    ("exile target artifact", 4.0),
    ("exile target enchantment", 4.0),
    ("artifact or enchantment", 4.0),
    ("from a graveyard", 3.5),
    ("graveyards", 3.5),
    ("exile all cards from", 3.5),
    ("counter target spell", 3.0),
    ("counter target creature spell", 3.0),
    ("destroy all creatures", 2.5),
    ("can't be countered", 2.0),
    ("protection from", 2.0),
    ("hexproof", 1.5),
    ("you gain", 1.0),
)

SIDEBOARD_CURVE_CAP = 5.0  # a 6-drop is never a sideboard card


def sideboard_value(card: OwnedCard) -> float:
    """How much this card wants to be in the 15 rather than the 60."""
    if "Land" in card.type_line or card.cmc > SIDEBOARD_CURVE_CAP:
        return float("-inf")
    text = card.oracle_text.lower()
    bonus = sum(weight for pattern, weight in _HATE_PATTERNS if pattern in text)
    # Rate still matters — a blank card is not a sideboard card either.
    return bonus + card_power(card) * 0.4


def build_sideboard(leftovers: list[DeckEntry], colors: set, size: int) -> list[DeckEntry]:
    """Fill `size` sideboard slots from the copies the main deck did not take.

    `leftovers` are the merged entries after the main build has decremented
    them, so the 4-copy cap is already honoured across both boards.
    """
    if size <= 0:
        return []
    candidates = [
        e for e in leftovers
        if e.count > 0 and _castable(e.card, colors) and not _is_basic_land(e.card)
        and sideboard_value(e.card) > float("-inf")
    ]
    candidates.sort(key=lambda e: sideboard_value(e.card), reverse=True)

    board: list[DeckEntry] = []
    total = 0
    for e in candidates:
        if total >= size:
            break
        # Two copies is the usual sideboard allotment — spread across more
        # answers rather than stacking four of one narrow card.
        take = min(e.count, 2, size - total)
        if take <= 0:
            continue
        board.append(DeckEntry(card=e.card, count=take))
        e.count -= take
        total += take

    # Still short (small collection): go back for the extra copies we skipped.
    for e in candidates:
        if total >= size:
            break
        take = min(e.count, size - total)
        if take <= 0:
            continue
        existing = next((b for b in board if b.card.name == e.card.name), None)
        if existing:
            existing.count += take
        else:
            board.append(DeckEntry(card=e.card, count=take))
        e.count -= take
        total += take
    return board


# ── Main builder ─────────────────────────────────────────────────────────────

def build_standard_deck(owned: list, fmt: str = "standard",
                        colors: set | None = None) -> tuple[list[DeckEntry], set, list[DeckEntry]]:
    """Build a constructed deck from the collection.

    Returns (main deck entries, colors used, sideboard entries).
    """
    spec = formats.get(fmt)
    entries = legal_pool(merge_by_name(owned, spec.max_copies), spec.key)
    if colors is None:
        colors = choose_colors(entries)

    pool = _color_pool(entries, colors)
    scored = sorted(
        pool,
        key=lambda e: card_power(e.card) + synergy_boost(e.card, pool),
        reverse=True,
    )

    deck: list[DeckEntry] = []
    curve_used = {k: 0 for k in CURVE_CAPS}
    interaction = 0
    total = 0

    def bucket(cmc: float) -> int:
        return min(int(cmc), 6)

    # Two passes: reserve slots for interaction first so aggro fluff doesn't
    # crowd out removal; the second pass fills the rest purely by score
    # (which may add interaction beyond the minimum if it's the best option).
    for first_pass in (True, False):
        for e in scored:
            if total >= NONLAND_TARGET:
                break
            if first_pass:
                if not _is_interaction(e.card):
                    continue
                if interaction >= MIN_INTERACTION:
                    break
            b = bucket(e.card.cmc)
            room = min(e.count, CURVE_CAPS[b] - curve_used[b], NONLAND_TARGET - total)
            if room <= 0:
                continue
            deck.append(DeckEntry(card=e.card, count=room))
            e.count -= room
            curve_used[b] += room
            total += room
            if first_pass:
                interaction += room

    lands = _build_mana_base(colors, deck, entries, spec.deck_size - total)
    sideboard = build_sideboard(entries, colors, spec.sideboard)
    return deck + lands, colors, sideboard
