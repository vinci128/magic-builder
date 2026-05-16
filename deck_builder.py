from collection import OwnedCard

# Target slot counts for non-land cards
NONBASIC_LAND_TARGET = 24   # owned non-basic lands from collection
BASIC_LAND_TARGET    = 12   # auto-added basic lands (total lands = 36)
RAMP_TARGET  = 10
DRAW_TARGET  = 8
REMOVAL_TARGET = 8
# Remaining slots go to synergy/value cards

BASIC_LAND_NAMES = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}


# ── Card-type detectors ──────────────────────────────────────────────────────

def _is_land(card: OwnedCard) -> bool:
    return "Land" in card.type_line


def _is_basic_land(card: OwnedCard) -> bool:
    tl = card.type_line
    return "Basic Land" in tl or tl in (
        "Basic Land — Plains", "Basic Land — Island", "Basic Land — Swamp",
        "Basic Land — Mountain", "Basic Land — Forest", "Basic Land — Wastes",
    )


def _is_ramp(card: OwnedCard) -> bool:
    t = card.oracle_text.lower()
    return (
        "add {" in t
        or ("search your library for" in t and "land" in t)
        or "untap target land" in t
    )


def _is_card_draw(card: OwnedCard) -> bool:
    t = card.oracle_text.lower()
    return "draw a card" in t or "draw cards" in t or "draw two" in t or "draw three" in t


def _is_removal(card: OwnedCard) -> bool:
    t = card.oracle_text.lower()
    # Exclude blinks (exile your own creature to return it)
    if "exile target creature you control" in t:
        return False
    # Exclude graveyard hate (not board removal)
    if "exile target card from a graveyard" in t:
        return False
    if "exile target permanent from a graveyard" in t:
        return False
    return (
        "destroy target creature" in t
        or "destroy target permanent" in t
        or "destroy target nonland" in t
        or "exile target creature you don't control" in t
        or "exile target creature an opponent controls" in t
        or "exile target creature." in t    # exile any creature (no qualifier)
        or "exile target creature," in t    # exile any creature + additional clause
        or "exile target permanent you don't control" in t
        or "exile target nonland permanent" in t
        or "counter target creature spell" in t
        or "counter target spell" in t
        or "deals damage to any target" in t
        or "deals damage to target creature" in t
        or "return target creature an opponent controls" in t
        or "destroy all" in t              # board wipes
        or "exile all creatures" in t
    )


# ── Synergy scoring ──────────────────────────────────────────────────────────

def _creature_types(type_line: str) -> set:
    if "Creature" not in type_line:
        return set()
    parts = type_line.split("—")
    return set(parts[1].strip().split()) if len(parts) > 1 else set()


def synergy_score(card: OwnedCard, commander: OwnedCard) -> float:
    score = 0.0
    cmd_kw = {k.lower() for k in commander.keywords}
    card_kw = {k.lower() for k in card.keywords}

    # Keyword overlap with reduced weight — generic keywords (e.g. Vigilance) are noise
    score += len(cmd_kw & card_kw) * 1.0

    # Word overlap filtered to meaningful words only (len > 4 skips articles, preps, etc.)
    cmd_words = {w for w in commander.oracle_text.lower().split() if len(w) > 4}
    card_words = {w for w in card.oracle_text.lower().split() if len(w) > 4}
    score += len(cmd_words & card_words) * 0.3

    # Tribal synergy
    score += len(_creature_types(commander.type_line) & _creature_types(card.type_line)) * 3.0

    # CMC preference — cheap creatures mean more casts per game
    if card.cmc <= 4:
        score += (4.0 - card.cmc) * 0.5

    t = card.oracle_text.lower()
    cmd_text = commander.oracle_text.lower()

    # Commander draws on creature cast → ETB value creatures are especially powerful
    # (each cast draws a card AND triggers the ETB; commander can bounce to replay)
    if "whenever you cast a creature spell" in cmd_text:
        if "when this creature enters" in t or (
            "when this enters" in t and "Creature" in card.type_line
        ):
            score += 3.0
        # Flash creatures can be cast on opponent's turn for extra draw triggers
        if "flash" in card_kw:
            score += 2.0
        # Land fetch on ETB feeds the commander's "put a land from hand" ability
        if ("search your library for" in t and "land" in t) or "lander token" in t:
            score += 1.5
        # Bounce to hand lets ETBs be replayed via the commander's activated ability
        if "return" in t and ("your hand" in t or "owner's hand" in t) and "creature" in t:
            score += 1.0

    # Commander drops lands from hand → Landfall triggers every time
    if "put a land card from your hand onto the battlefield" in cmd_text:
        if "landfall" in card_kw:
            score += 3.0

    # ── Penalties for anti-synergies ────────────────────────────────────────────

    # Ripple is useless in a singleton format
    if "ripple" in card_kw:
        score -= 3.0

    # Self-mill for colorless mana is a bad rate and mills away combo pieces
    if "mill a card" in t and "add {c}" in t:
        score -= 2.5

    # Giving opponents 1/1 tokens every time you tap a land for mana
    if "whenever you tap this" in t and "opponent" in t and "creature token" in t:
        score -= 2.0

    # Equipment draw engine needs Equipment support that this commander doesn't provide
    if "whenever an equipment" in t and "draw a card" in t:
        if "equipment" not in cmd_text and "equip" not in cmd_text:
            score -= 2.0

    # Angel tribal synergy without Angels in the deck
    if "whenever an angel you control" in t and "angel" not in cmd_text:
        score -= 2.0

    return score


# ── Basic land generation ────────────────────────────────────────────────────

def _make_basic(color: str, index: int) -> OwnedCard:
    name = BASIC_LAND_NAMES.get(color, "Wastes")
    return OwnedCard(
        name=name,
        scryfall_id=f"_basic_{color}_{index}",
        quantity=1,
        type_line=f"Basic Land — {name}" if color in BASIC_LAND_NAMES else "Basic Land — Wastes",
        color_identity=[color] if color in BASIC_LAND_NAMES else [],
        legalities={"commander": "legal"},
        is_basic_filler=True,
    )


def _optimal_basics(ci: set, non_land_deck: list, count: int) -> list:
    """Generate `count` basic lands distributed by color demand of the non-land cards."""
    colors = [c for c in ("W", "U", "B", "R", "G") if c in ci]
    if not colors:
        return [_make_basic("C", i) for i in range(count)]

    # Count how many non-land cards are of each color to weight basics
    weight: dict[str, int] = {c: 0 for c in colors}
    for card in non_land_deck:
        for c in card.color_identity:
            if c in weight:
                weight[c] += 1
    # If all weights are 0, fall back to even distribution
    if sum(weight.values()) == 0:
        weight = {c: 1 for c in colors}

    total = sum(weight.values())
    basics: list[OwnedCard] = []
    allocated = 0
    for i, color in enumerate(colors):
        if i == len(colors) - 1:
            n = count - allocated
        else:
            n = round(count * weight[color] / total)
        for j in range(n):
            basics.append(_make_basic(color, allocated + j))
        allocated += n

    return basics[:count]


# ── Main deck-builder ────────────────────────────────────────────────────────

def build_deck(commander: OwnedCard, owned_cards: list) -> list:
    ci = set(commander.color_identity)

    # Eligible pool: matching color identity, Commander-legal, not the commander,
    # and NOT basic lands (those are added automatically)
    pool = [
        c for c in owned_cards
        if c.scryfall_id != commander.scryfall_id
        and set(c.color_identity).issubset(ci)
        and c.legalities.get("commander") == "legal"
        and not _is_basic_land(c)
    ]

    non_basic_lands = [c for c in pool if _is_land(c)]
    non_lands = [c for c in pool if not _is_land(c)]

    scored_non_lands  = sorted(non_lands,       key=lambda c: synergy_score(c, commander), reverse=True)
    scored_lands      = sorted(non_basic_lands, key=lambda c: synergy_score(c, commander), reverse=True)

    used_ids: set[str] = set()

    def pick(source: list, target: int) -> list:
        chosen = []
        for c in source:
            if c.scryfall_id not in used_ids:
                chosen.append(c)
                used_ids.add(c.scryfall_id)
                if len(chosen) == target:
                    break
        return chosen

    ramp_cards    = pick([c for c in scored_non_lands if _is_ramp(c)],     RAMP_TARGET)
    draw_cards    = pick([c for c in scored_non_lands if _is_card_draw(c)], DRAW_TARGET)
    remove_cards  = pick([c for c in scored_non_lands if _is_removal(c)],   REMOVAL_TARGET)
    nonbasic_lands = pick(scored_lands, NONBASIC_LAND_TARGET)
    synergy_cards = pick(scored_non_lands, 99)  # pick() skips already-used ids

    # Assemble non-basic portion
    non_basic_deck = nonbasic_lands + ramp_cards + draw_cards + remove_cards + synergy_cards

    # Total land slots = NONBASIC_LAND_TARGET (from collection) + BASIC_LAND_TARGET (auto)
    # But if we couldn't fill the non-basic land target, add more basics to compensate
    actual_nonbasic_lands = len(nonbasic_lands)
    basic_count = (NONBASIC_LAND_TARGET - actual_nonbasic_lands) + BASIC_LAND_TARGET

    # Total deck must be 99; fill remaining slots with basics if non-basic pool is small
    total_non_basic = len(non_basic_deck)
    if total_non_basic > 99 - basic_count:
        non_basic_deck = non_basic_deck[:99 - basic_count]

    # If even after basics we're short, add extra basics
    basic_count = 99 - len(non_basic_deck)

    basics = _optimal_basics(ci, non_basic_deck, basic_count)
    deck = non_basic_deck + basics

    return deck[:99]
