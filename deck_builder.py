from typing import NamedTuple

import archetype
import brackets
import formats
from collection import OwnedCard

# Target slot counts for non-land cards
NONBASIC_LAND_TARGET = 24   # owned non-basic lands from collection
BASIC_LAND_TARGET    = 12   # auto-added basic lands (total lands = 36)
RAMP_TARGET  = 10
DRAW_TARGET  = 8
REMOVAL_TARGET = 8
# Tutors are a role like the three above. Without a slot of their own they only
# ever compete in the synergy fill, where they are scored on typal overlap with
# the commander — so a creature tutor in a party deck loses its place to a
# Guildgate. Small by default, because tutors are a tool and not a plan.
TUTOR_TARGET = 3
# ...but how many belong in a deck is a bracket question, which is the one
# thing the default cannot express. Bracket 4 is "play your best cards, every
# game": a deck that owns Demonic Tutor and runs three search effects total is
# not optimized, it is a good bracket 3 deck, and CRISPI reads it that way —
# its search ladder needs roughly eight live tutors before the Consistency
# column clears 8. Brackets 1 and 2 lean the other way: finding the same card
# every game is exactly what a casual table does not want to sit across from.
TUTOR_TARGET_BY_BRACKET = {1: 2, 2: 2, 3: 3, 4: 8, 5: 8}
# Remaining slots go to synergy/value cards


def tutor_target(target_bracket: int | None) -> int:
    """How many tutor slots a build aimed at `target_bracket` gets."""
    if target_bracket is None:
        return TUTOR_TARGET
    return TUTOR_TARGET_BY_BRACKET.get(target_bracket, TUTOR_TARGET)

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

class SynergyReason(NamedTuple):
    """One scoring term. `key` groups the same reason across cards."""

    key: str
    label: str
    points: float


# Generic names for each `key`, for summarising a whole deck. The per-card
# labels carry specifics (which keywords, which tribe) that don't generalise.
SYNERGY_THEME_LABELS = {
    "keywords": "Shared keywords",
    "text": "Echoes the commander's rules text",
    "tribal": "Tribal overlap",
    "cheap": "Cheap enough to redeploy",
    "etb": "Enters-the-battlefield value",
    "flash": "Flash",
    "landfetch": "Land fetch",
    "bounce": "Bounce to replay entry triggers",
    "landfall": "Landfall",
    "ripple": "Ripple — dead in singleton",
    "selfmill": "Self-mill for colourless mana",
    "gifts": "Gives opponents tokens",
    "equipment": "Equipment payoff with no Equipment",
    "angels": "Angel payoff with no Angels",
}

# Terms that fire on most of the deck and so say nothing about its theme:
# `cheap` is really a curve statement (the mana curve chart already shows it),
# and `text` trips on any shared five-letter word, which is a weak enough
# signal that it drowns out tribal and keyword overlap. Both still score, and
# both still appear per card where they are true — they just don't headline.
GENERIC_SYNERGY_THEMES = {"cheap", "text"}


def _creature_types(type_line: str) -> set:
    if "Creature" not in type_line:
        return set()
    parts = type_line.split("—")
    return set(parts[1].strip().split()) if len(parts) > 1 else set()


def synergy_reasons(card: OwnedCard, commander: OwnedCard) -> list["SynergyReason"]:
    """Every scoring term that fired for this card.

    `synergy_score` is the sum of these, so the two can never drift apart — and
    the web UI can show a player *why* a card made the deck.
    """
    reasons: list[SynergyReason] = []
    cmd_kw = {k.lower() for k in commander.keywords}
    card_kw = {k.lower() for k in card.keywords}

    # Keyword overlap with reduced weight — generic keywords (e.g. Vigilance) are noise
    shared_kw = cmd_kw & card_kw
    if shared_kw:
        listed = ", ".join(sorted(k.title() for k in shared_kw))
        reasons.append(SynergyReason("keywords", f"Shares {listed} with the commander",
                                     len(shared_kw) * 1.0))

    # Word overlap filtered to meaningful words only (len > 4 skips articles, preps, etc.)
    cmd_words = {w for w in commander.oracle_text.lower().split() if len(w) > 4}
    card_words = {w for w in card.oracle_text.lower().split() if len(w) > 4}
    shared_words = cmd_words & card_words
    if shared_words:
        reasons.append(SynergyReason(
            "text", f"Rules text echoes the commander ({len(shared_words)} terms)",
            len(shared_words) * 0.3))

    # Tribal synergy
    shared_types = _creature_types(commander.type_line) & _creature_types(card.type_line)
    if shared_types:
        listed = "/".join(sorted(shared_types))
        reasons.append(SynergyReason("tribal", f"{listed} tribal", len(shared_types) * 3.0))

    # CMC preference — cheap creatures mean more casts per game
    if card.cmc <= 4:
        reasons.append(SynergyReason("cheap", f"Cheap to cast (mana value {int(card.cmc)})",
                                     (4.0 - card.cmc) * 0.5))

    t = card.oracle_text.lower()
    cmd_text = commander.oracle_text.lower()

    # Commander draws on creature cast → ETB value creatures are especially powerful
    # (each cast draws a card AND triggers the ETB; commander can bounce to replay)
    if "whenever you cast a creature spell" in cmd_text:
        if "when this creature enters" in t or (
            "when this enters" in t and "Creature" in card.type_line
        ):
            reasons.append(SynergyReason(
                "etb", "Enters-the-battlefield value the commander pays you for", 3.0))
        # Flash creatures can be cast on opponent's turn for extra draw triggers
        if "flash" in card_kw:
            reasons.append(SynergyReason(
                "flash", "Flash — casts on their turn for extra draw triggers", 2.0))
        # Land fetch on ETB feeds the commander's "put a land from hand" ability
        if ("search your library for" in t and "land" in t) or "lander token" in t:
            reasons.append(SynergyReason(
                "landfetch", "Fetches a land for the commander to put into play", 1.5))
        # Bounce to hand lets ETBs be replayed via the commander's activated ability
        if "return" in t and ("your hand" in t or "owner's hand" in t) and "creature" in t:
            reasons.append(SynergyReason(
                "bounce", "Returns creatures to hand so ETBs can be replayed", 1.0))

    # Commander drops lands from hand → Landfall triggers every time
    if "put a land card from your hand onto the battlefield" in cmd_text:
        if "landfall" in card_kw:
            reasons.append(SynergyReason(
                "landfall", "Landfall, and the commander drops extra lands", 3.0))

    # ── Penalties for anti-synergies ────────────────────────────────────────────

    # Ripple is useless in a singleton format
    if "ripple" in card_kw:
        reasons.append(SynergyReason("ripple", "Ripple does nothing in a singleton deck", -3.0))

    # Self-mill for colorless mana is a bad rate and mills away combo pieces
    if "mill a card" in t and "add {c}" in t:
        reasons.append(SynergyReason("selfmill", "Mills your own deck for colourless mana", -2.5))

    # Giving opponents 1/1 tokens every time you tap a land for mana
    if "whenever you tap this" in t and "opponent" in t and "creature token" in t:
        reasons.append(SynergyReason("gifts", "Hands opponents creature tokens", -2.0))

    # Equipment draw engine needs Equipment support that this commander doesn't provide
    if "whenever an equipment" in t and "draw a card" in t:
        if "equipment" not in cmd_text and "equip" not in cmd_text:
            reasons.append(SynergyReason(
                "equipment", "Wants Equipment this commander doesn't provide", -2.0))

    # Angel tribal synergy without Angels in the deck
    if "whenever an angel you control" in t and "angel" not in cmd_text:
        reasons.append(SynergyReason(
            "angels", "Wants Angels this commander doesn't provide", -2.0))

    return reasons


def synergy_score(card: OwnedCard, commander: OwnedCard) -> float:
    score = 0.0
    for reason in synergy_reasons(card, commander):
        score += reason.points
    return score


# Themes that read as a deck name. Terms not listed here are real synergies but
# describe a card, not an archetype ("Shares Flying with the commander").
SYNERGY_ARCHETYPES = {
    "etb": "Blink",
    "landfall": "Landfall",
    "flash": "Flash",
    "landfetch": "Ramp",
    "bounce": "Bounce",
}


def deck_archetype(deck: list, commander: OwnedCard) -> str:
    """Name a singleton deck after its colours and its strongest shared theme.

    Tribal wins when it fires, because that is how players name these decks:
    a Golgari deck full of Elves is a Golgari Elves deck.
    """
    points: dict[str, float] = {}
    tribes: dict[str, int] = {}
    nonland = weighted = interaction = 0

    for card in deck:
        if card.is_basic_filler:
            continue
        if not _is_land(card):
            nonland += 1
            weighted += card.cmc
            if _is_removal(card):
                interaction += 1
        for reason in synergy_reasons(card, commander):
            if reason.points <= 0 or reason.key in GENERIC_SYNERGY_THEMES:
                continue
            points[reason.key] = points.get(reason.key, 0.0) + reason.points
            if reason.key == "tribal":
                for tribe in _creature_types(card.type_line) & _creature_types(commander.type_line):
                    tribes[tribe] = tribes.get(tribe, 0) + 1

    theme = None
    if tribes and points.get("tribal", 0) >= 9:  # three creatures' worth of overlap
        theme = max(tribes.items(), key=lambda kv: kv[1])[0] + "s"
    else:
        ranked = [(pts, SYNERGY_ARCHETYPES[key]) for key, pts in points.items()
                  if key in SYNERGY_ARCHETYPES]
        if ranked and max(ranked)[0] >= 9:
            theme = max(ranked)[1]

    avg_cmc = (weighted / nonland) if nonland else None
    share = (interaction / nonland) if nonland else 0.0
    return archetype.deck_name(commander.color_identity, theme,
                               avg_cmc=avg_cmc, interaction_share=share)


# ── Basic land generation ────────────────────────────────────────────────────

def _make_basic(color: str, index: int) -> OwnedCard:
    name = BASIC_LAND_NAMES.get(color, "Wastes")
    return OwnedCard(
        name=name,
        scryfall_id=f"_basic_{color}_{index}",
        quantity=1,
        type_line=f"Basic Land — {name}" if color in BASIC_LAND_NAMES else "Basic Land — Wastes",
        color_identity=[color] if color in BASIC_LAND_NAMES else [],
        # Basic lands are legal in every format, and free.
        legalities={fmt: "legal" for fmt in formats.FORMATS},
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


# ── Building to a bracket ────────────────────────────────────────────────────

def _bracket_pool(pool: list, commander: OwnedCard, target: int) -> tuple[list, list]:
    """Trim `pool` to what a deck aiming at bracket `target` may contain.

    Returns the trimmed pool and the Game Changers to seed into it. Seeding is
    the half that is easy to forget: brackets 1 and 2 are reached by leaving
    cards out, but 3 is reached by putting them in — a deck with no Game Changer
    and no combo is a 2 however carefully it was built.

    Only the card-level criteria are decided here. Two-card combos depend on
    which pairs end up together and are only known once Commander Spellbook has
    answered, so `main.py` reports them against the target rather than building
    around them.
    """
    def best(cards: list, limit: int) -> list:
        return sorted(cards, key=lambda c: synergy_score(c, commander),
                      reverse=True)[:limit]

    if target >= 4:
        # Nothing above 3 restricts what a deck may contain, so nothing is
        # trimmed. The Game Changers are still seeded: "bring out your strongest
        # cards" is the whole of bracket 4, and the synergy score never picks
        # them on its own.
        game_changers = [c for c in pool if brackets.is_game_changer(c)]
        return pool, best(game_changers, len(game_changers))

    keep, game_changers, extra_turns = [], [], []
    for card in pool:
        # Never allowed below 4, at any of the lower brackets.
        if brackets.is_mass_land_denial(card) or brackets.is_chainable_extra_turn(card):
            continue
        if brackets.is_game_changer(card):
            game_changers.append(card)
        elif brackets.is_extra_turn(card):
            extra_turns.append(card)
        else:
            keep.append(card)

    # Bracket 3 may run up to three Game Changers; 1 and 2 may run none.
    seeds = best(game_changers, brackets.MAX_GAME_CHANGERS_B3) if target == 3 else []
    # Bracket 1 allows no extra-turn cards at all; 2 and 3 want low quantities.
    turns = [] if target == 1 else best(extra_turns, brackets.MAX_EXTRA_TURNS_B3)
    return keep + seeds + turns, seeds


MAX_TUTOR_REPAIRS = 3   # swapping one tutor can strand another; converge, don't loop


def _targets_in(pool: list, words: list) -> int:
    """How many cards in `pool` a tutor restricted to `words` could find."""
    if not words:
        return len(pool)
    return sum(1 for c in pool
               if any(w in c.type_line.lower() for w in words))


def _by_tutor_quality(pool: list) -> list:
    """Tutors, best first: unrestricted, then most to find, then cheapest.

    Deliberately not the synergy order the rest of the build uses — what makes
    a tutor good is finding something useful for little mana, which has nothing
    to do with sharing a creature type with the commander.

    Target count is the middle term because cost alone gets it wrong: a
    two-mana artifact tutor looks better than a three-mana creature tutor until
    you notice the deck is half creatures and barely runs an artifact.
    """
    return sorted(
        (c for c in pool if brackets.is_tutor(c)),
        key=lambda c: (bool(brackets.tutor_restriction(c)),
                       -_targets_in(pool, brackets.tutor_restriction(c)),
                       c.cmc),
    )


def _replace_dead_tutors(deck: list, commander, pool: list, used_names: set) -> list:
    """Swap out tutors that can find nothing in the deck they ended up in.

    A restricted tutor is only worth a slot if the deck runs what it searches
    for, and the builder cannot know that while choosing cards — Honored
    Knight-Captain fetches an Equipment, and whether the deck has one is
    decided by the same pass that picked the Knight. So it is fixed afterwards:
    find the tutors with nothing to find, and spend their slots on the next
    cards down the list instead.

    Iterated a few times because a swap changes the deck the next tutor is
    judged against — dropping the only Equipment can strand an Equipment tutor
    that was live a moment ago.
    """
    out = list(deck)
    for _ in range(MAX_TUTOR_REPAIRS):
        context = ([commander] if commander is not None else []) + out
        dead = [c for c in out
                if brackets.is_tutor(c) and not brackets.is_live_tutor(c, context)]
        if not dead:
            break
        # A tutor slot should stay a tutor slot where it can, so a working
        # tutor is preferred to the next card down the synergy list.
        spare = [c for c in _by_tutor_quality(pool) if c.name not in used_names
                 and brackets.is_live_tutor(c, context)]
        spare += [c for c in pool if c.name not in used_names]
        if not spare:
            break
        for tutor in dead:
            # The pool holds one entry per printing, so the same name can appear
            # twice — re-check as we go or a swap can duplicate a card. The dead
            # tutor's own name stays reserved so it cannot come back.
            replacement = None
            while spare:
                candidate = spare.pop(0)
                if candidate.name not in used_names:
                    replacement = candidate
                    break
            if replacement is None:
                break
            out[out.index(tutor)] = replacement
            used_names.add(replacement.name)
    return out


def _seeded_first(scored: list, seeds: list) -> list:
    """Move `seeds` to the front of an already-scored list.

    The deck is assembled lands-first and truncated from the end, so a card at
    the front of its list is one that survives. Game Changers score no synergy
    points — Farewell shares nothing with an Elemental commander — and would
    otherwise lose every slot to a card that happens to be the right creature
    type.
    """
    names = {c.name for c in seeds}
    return seeds + [c for c in scored if c.name not in names]


# ── Main deck-builder ────────────────────────────────────────────────────────

def build_deck(commander: OwnedCard, owned_cards: list, fmt: str = "commander",
               target_bracket: int | None = None) -> list:
    """Build the singleton deck behind `commander` (Commander or Brawl).

    Both formats are 1 commander + 99, so only the legality key differs.

    `target_bracket` builds to a Commander bracket: it keeps out what that
    bracket does not allow, and for bracket 3 seeds in the Game Changers that
    are what put a deck there. Default None builds the strongest deck it can
    and lets the bracket fall where it falls.
    """
    spec = formats.get(fmt)
    if not spec.singleton:
        raise ValueError(f"{spec.label} is not a singleton format")
    size = spec.deck_size - 1
    ci = set(commander.color_identity)

    # Eligible pool: matching color identity, legal in this format, not the
    # commander, and NOT basic lands (those are added automatically)
    pool = [
        c for c in owned_cards
        if c.name != commander.name
        and set(c.color_identity).issubset(ci)
        and c.legalities.get(spec.key) == "legal"
        and not _is_basic_land(c)
    ]

    seeds: list = []
    if target_bracket is not None:
        pool, seeds = _bracket_pool(pool, commander, target_bracket)

    non_basic_lands = [c for c in pool if _is_land(c)]
    non_lands = [c for c in pool if not _is_land(c)]

    scored_non_lands  = sorted(non_lands,       key=lambda c: synergy_score(c, commander), reverse=True)
    scored_lands      = sorted(non_basic_lands, key=lambda c: synergy_score(c, commander), reverse=True)

    # A seeded Game Changer can be a land (Gaea's Cradle) or not (Farewell), so
    # each goes to the front of its own list and the land maths below is unmoved.
    if seeds:
        scored_non_lands = _seeded_first(scored_non_lands, [c for c in seeds if not _is_land(c)])
        scored_lands     = _seeded_first(scored_lands,     [c for c in seeds if _is_land(c)])

    # Commander is singleton: a second printing of a card is still a duplicate,
    # so reserve by name rather than by Scryfall id.
    used_names: set[str] = set()

    def pick(source: list, target: int) -> list:
        chosen = []
        for c in source:
            if c.name not in used_names:
                chosen.append(c)
                used_names.add(c.name)
                if len(chosen) == target:
                    break
        return chosen

    ramp_cards    = pick([c for c in scored_non_lands if _is_ramp(c)],     RAMP_TARGET)
    draw_cards    = pick([c for c in scored_non_lands if _is_card_draw(c)], DRAW_TARGET)
    remove_cards  = pick([c for c in scored_non_lands if _is_removal(c)],   REMOVAL_TARGET)
    tutor_cards   = pick(_by_tutor_quality(scored_non_lands),
                         tutor_target(target_bracket))
    nonbasic_lands = pick(scored_lands, NONBASIC_LAND_TARGET)
    synergy_cards = pick(scored_non_lands, 99)  # pick() skips already-used ids

    # Assemble non-basic portion. Synergy comes last because truncation cuts
    # from the end, so the role slots above are the ones that survive.
    non_basic_deck = (nonbasic_lands + ramp_cards + draw_cards + remove_cards
                      + tutor_cards + synergy_cards)

    # Total land slots = NONBASIC_LAND_TARGET (from collection) + BASIC_LAND_TARGET (auto)
    # But if we couldn't fill the non-basic land target, add more basics to compensate
    actual_nonbasic_lands = len(nonbasic_lands)
    basic_count = (NONBASIC_LAND_TARGET - actual_nonbasic_lands) + BASIC_LAND_TARGET

    # Fill remaining slots with basics if the non-basic pool is small
    total_non_basic = len(non_basic_deck)
    if total_non_basic > size - basic_count:
        non_basic_deck = non_basic_deck[:size - basic_count]

    # Whether a tutor can find anything is a property of the finished deck, so
    # it can only be judged once there is one. Runs after truncation so the
    # slot count is already final and a swap is one card for one card.
    non_basic_deck = _replace_dead_tutors(
        non_basic_deck, commander, scored_non_lands, used_names)

    # If even after basics we're short, add extra basics
    basic_count = size - len(non_basic_deck)

    basics = _optimal_basics(ci, non_basic_deck, basic_count)
    deck = non_basic_deck + basics

    return deck[:size]
