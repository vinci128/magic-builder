import formats
from collection import OwnedCard


def is_commander_eligible(card: OwnedCard, fmt: str = "commander") -> bool:
    spec = formats.get(fmt)
    if card.legalities.get(spec.key) != "legal":
        return False
    tl = card.type_line
    if "Legendary" not in tl:
        return False
    if "Creature" in tl:
        return True
    if "Planeswalker" not in tl:
        return False
    # Brawl lets any legendary planeswalker lead; Commander needs the card to
    # say so itself.
    if fmt == "brawl":
        return True
    return "can be your commander" in card.oracle_text.lower()


def _edhrec_popularity(name: str) -> float:
    """Return EDHREC deck count for a commander name, or 0 on failure."""
    try:
        from pyedhrec import EDHRec
        edh = EDHRec()
        data = edh.get_commander_data(name)
        return min(data.get("num_decks", 0) / 500.0, 100.0)
    except Exception:
        return 0.0


def score_commander(candidate: OwnedCard, all_cards: list, use_popularity: bool = True,
                    fmt: str = "commander") -> float:
    key = formats.get(fmt).key
    ci = set(candidate.color_identity)
    compatible = sum(
        1 for c in all_cards
        if c.scryfall_id != candidate.scryfall_id
        and set(c.color_identity).issubset(ci)
        and c.legalities.get(key) == "legal"
    )
    popularity = _edhrec_popularity(candidate.name) if use_popularity else 0.0
    return compatible + popularity


def find_commanders(owned_cards: list, use_popularity: bool = True,
                    fmt: str = "commander") -> list:
    """Return list of (OwnedCard, score) sorted best-first.

    `use_popularity` adds an EDHREC deck-count bonus — one network call per
    candidate, so callers that need a fast answer can turn it off.
    """
    candidates = [c for c in owned_cards if is_commander_eligible(c, fmt)]
    scored = [(c, score_commander(c, owned_cards, use_popularity, fmt)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
