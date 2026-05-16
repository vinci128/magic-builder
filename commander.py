from collection import OwnedCard


def is_commander_eligible(card: OwnedCard) -> bool:
    if card.legalities.get("commander") != "legal":
        return False
    tl = card.type_line
    if "Legendary" not in tl:
        return False
    if "Creature" in tl:
        return True
    # Planeswalkers that explicitly grant commander status via oracle text
    if "Planeswalker" in tl and "can be your commander" in card.oracle_text.lower():
        return True
    return False


def _edhrec_popularity(name: str) -> float:
    """Return EDHREC deck count for a commander name, or 0 on failure."""
    try:
        from pyedhrec import EDHRec
        edh = EDHRec()
        data = edh.get_commander_data(name)
        return min(data.get("num_decks", 0) / 500.0, 100.0)
    except Exception:
        return 0.0


def score_commander(candidate: OwnedCard, all_cards: list) -> float:
    ci = set(candidate.color_identity)
    compatible = sum(
        1 for c in all_cards
        if c.scryfall_id != candidate.scryfall_id
        and set(c.color_identity).issubset(ci)
        and c.legalities.get("commander") == "legal"
    )
    popularity = _edhrec_popularity(candidate.name)
    return compatible + popularity


def find_commanders(owned_cards: list) -> list:
    """Return list of (OwnedCard, score) sorted best-first."""
    candidates = [c for c in owned_cards if is_commander_eligible(c)]
    scored = [(c, score_commander(c, owned_cards)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
