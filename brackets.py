"""Commander bracket (1-5) estimation for a built singleton deck.

The bracket system is WotC's shared vocabulary for how hard a Commander deck is
trying. Each bracket pairs an expected game length with a list of what a deck
may contain, so this module tests those restrictions and reports the lowest
bracket the deck still fits:

    1 Exhibition  9+ turns.  No Game Changers, no mass land denial, no
                  extra-turn cards, no two-card infinite combos
    2 Core        8+ turns.  As above, but a few extra-turn cards are fine so
                  long as they are not chained
    3 Upgraded    6+ turns.  Up to 3 Game Changers, no mass land denial, no
                  chained extra turns, and any two-card combo must be late-game
    4 Optimized   4+ turns.  No restrictions
    5 cEDH        Any turn.  No restrictions, plus intent to compete in a
                  tournament metagame

Current as of the February 9, 2026 beta update. The turn expectations, and the
removal of the tutor guiderails, came in the October 21, 2025 update; February
2026 changed only the Game Changers list, which we read from Scryfall and so
never have to track by hand.

Why the floor is 2, not 1
-------------------------
Two of the five brackets are never assigned here, for the same reason: they say
*why* a deck was built, which no decklist shows.

Bracket 5 is the obvious one — only a tournament mindset separates it from 4.
Bracket 1 is the subtler one. Exhibition is not "a deck that happens to break
none of the rules": it is the bracket for a deck whose theme was chosen ahead of
its power, expecting games to run nine turns or more. Nearly every casual deck
ever built clears bracket 1's restriction list without being an Exhibition deck
— every precon does, which is how three bracket-2 precons all came out as 1
here. The restrictions simply do not separate 1 from 2, so a classifier that
stops at the first list a deck satisfies calls every clean deck an Exhibition
deck. The other tools reach the same conclusion from the other end: automatic
detection on Moxfield and Archidekt effectively never assigns a 1.

The floor is therefore 2, which is also the wider target it now has to be: the
October 2025 update cut the "average preconstructed deck" wording from Core and
opened the bracket to upgraded and semi-tuned decks as well. When a deck also
clears bracket 1's stricter rules that is reported as a note the pilot may
claim, not as a verdict.

How fast it wins
----------------
The "lowest list that fits" rule under-reads decks the other way: a deck with
fast mana and a pile of one-mana answers but no Game Changer is a 2 by the
letter of the restrictions while being able to close a game well before the
eighth turn that bracket 2 asks for. Since October 2025 that game length is the
framework's headline expectation, not a footnote, so `_speed_signals` looks for
the things that shorten a game — cheap mana, free interaction, a low curve — and
adds an advisory criterion. It does not move the number: the number stays the
published, checkable one, and the speed read is offered next to it.

Where the numbers come from
---------------------------
Game Changers are a published list, read off Scryfall's `game_changer` field,
so that criterion is exact. Mass land denial, extra turns and tutors are
inferred from rules text, in the style of `deck_builder.py`'s role detectors:
good enough to sort decks, not a rules oracle. Two-card combos come from
Commander Spellbook via `combos.py`, and are reported as unchecked rather than
as absent when that lookup could not run.

Tutors are not a bracket restriction at all. The October 2025 update removed the
tutor guiderails from every bracket, reasoning that the efficient tutors are
Game Changers (already counted above) and that a pile of inefficient ones does
not warp a table. They are still counted here, as context only.

The thresholds below (how many extra turns stop being "low quantities", what
counts as a fast deck) are this module's own reading — WotC gives those limits
in words, not numbers, and says outright that breaking an expectation once does
not put a deck in another bracket.
"""

import re
from typing import NamedTuple

from combos import EARLY_TURN

BRACKET_NAMES = {
    1: "Exhibition",
    2: "Core",
    3: "Upgraded",
    4: "Optimized",
    5: "cEDH",
}

BRACKET_BLURBS = {
    1: "Ultra-casual: theme chosen ahead of power, with games running 9+ turns.",
    2: "The classic casual game — a real strategy, winning on turn 8 or later.",
    3: "Upgraded: strong synergy and card quality, winning from turn 6.",
    4: "Optimized: lethal, fast and consistent, winning from turn 4.",
    5: "cEDH: built to beat the tournament metagame, and can win on any turn.",
}

# The turn each bracket expects a game to last at least this long, from the
# October 2025 update. Bracket 5 has none: those games can end on any turn.
BRACKET_TURNS = {1: 9, 2: 8, 3: 6, 4: 4}

MAX_GAME_CHANGERS_B3 = 3   # published: bracket 3 allows up to three
MAX_EXTRA_TURNS_B3 = 3     # our reading of "low quantities" — more is a plan
SPEED_SIGNALS_FAST = 2     # how many speed signals read as "ends games early"


# ── Card-level detectors ─────────────────────────────────────────────────────

# Sweeps and locks that take everyone's lands away. Spot land removal
# ("destroy target land") is deliberately absent: one Ghost Quarter is not a
# land-denial plan, and the bracket rules are about the plan.
# The land clause is matched inside the sweep it belongs to, so a wipe that
# takes lands along with everything else counts (Jokulhaups: "destroy all
# artifacts, creatures, and lands"), while one that spares them does not
# ("destroy all nonland permanents" — `\blands\b` will not match "nonland").
# The symmetric sacrifices need a quantity, for the same reason spot removal is
# absent: "each player sacrifices a land" once (Hurloon Shaman, Tremble) costs
# everyone the same single land and locks nobody out of the game. It becomes
# denial when it takes several at once, scales with something, or repeats.
_MLD_PATTERNS = (
    r"(destroy|exile|return) all [^.]{0,40}\blands\b",
    r"each (player|opponent) sacrifices (two|three|four|five|six|seven|all|half|x|\d+) lands?",
    r"each (player|opponent) sacrifices [^.]{0,40}lands?[^.]{0,25}for each",
    r"upkeep, (that|each) player sacrifices a land",  # Mana Vortex — a lock
    # The lock has to be the standing kind. "During their controllers' untap
    # steps" is Winter Orb and Static Orb; "during its controller's *next*
    # untap step" is one tapped permanent for one turn, which is not denial.
    r"\blands don't untap during their controllers' untap steps",
    r"permanents don't untap during their controllers' untap steps",
    # The same standing lock under newer templating: Winter Orb and Static Orb
    # were re-worded to "can't untap more than", and Stasis skips the step
    # outright. Counting these is consistent with counting Hokori above.
    r"players can't untap more than \w+ (land|permanent)",
    r"players skip their untap steps",
)
_MLD_RE = re.compile("|".join(_MLD_PATTERNS))

# "Destroy all permanents" (Obliterate) names no land but takes every one.
_WIPE_INCLUDES_LANDS = re.compile(r"(destroy|exile) all permanents")

# A sweep that mentions lands only to spare them is the opposite of land
# denial: Scourglass ("except for artifacts and lands"), Elspeth Tirel
# ("except for lands and tokens"), Embargo ("Nonland permanents don't untap").
_SPARES_LANDS = re.compile(
    r"except for [^.]{0,30}lands|(nonland|snow) permanents don't untap"
)

_EXTRA_TURN_RE = re.compile(r"takes? an extra turn|take an extra turn after this one")

# An extra-turn spell that exiles itself on resolution cannot be chained, which
# is the thing brackets 2 and 3 actually forbid.
_SELF_EXILE_RE = re.compile(r"exile (it|this card)(\.| instead)")

# Tutoring: fetching *a card* out of the library, however the search is worded —
# "for a card", "for a creature card", "and graveyard for an artifact card". The
# qualifier between "for a" and "card" is captured so land fetch can be dropped:
# every ramp deck searches up a Forest and the bracket rules do not count it.
# "an" comes before "a": leftmost alternation wins, so `a|an` would match the
# "a" of "an equipment card" and leave "n equipment" as the qualifier — and a
# stray "n" matches almost every type line on earth.
_TUTOR_RE = re.compile(
    r"search your library[^.]{0,25}?for (?:an|a|up to \w+)([^.,;]{0,40}?)\bcards?\b"
)
_LAND_FETCH_RE = re.compile(
    r"\b(land|lands|basic|plains|island|swamp|mountain|forest|gate)\b"
)

# ── Speed signals (our own reading; see the module docstring) ────────────────

# Fast mana. Sol Ring is deliberately absent: it is in nearly every deck ever
# assembled, so its presence says nothing about how fast this one is.
_FAST_MANA = frozenset((
    "mana vault", "grim monolith", "chrome mox", "mox diamond", "mox opal",
    "mox amber", "mox tantalite", "lotus petal", "jeweled lotus", "lion's eye diamond",
    "ancient tomb", "city of traitors", "gaea's cradle", "serra's sanctum",
    "tolarian academy", "dark ritual", "cabal ritual", "rite of flame",
    "pyretic ritual", "desperate ritual", "simian spirit guide",
    "elvish spirit guide", "springleaf drum", "carpet of flowers",
))

# Free spells — the alternative-cost ones (Force of Will, Fierce Guardianship),
# not cascade, which uses the same words about a *different* card.
_FREE_SPELL_RE = re.compile(
    r"cast this spell without paying its mana cost|"
    r"rather than pay (this spell's|its) mana cost"
)

# One- and two-mana answers. A deck that can answer cheaply protects a fast win
# and survives to take one; the density of them says more than any single card.
_INTERACTION_RE = re.compile(
    r"counter target|destroy target|exile target|"
    r"sacrifices? a (creature|permanent)|return target .{0,20}to its owner's hand"
)

MIN_FAST_MANA_FAST = 2
MIN_FREE_SPELLS_FAST = 2
MIN_CHEAP_ANSWERS_FAST = 8
MAX_AVG_MV_FAST = 2.6       # a bracket 2 deck usually sits above 3


def _text(card) -> str:
    return (card.oracle_text or "").lower()


def is_game_changer(card) -> bool:
    return bool(getattr(card, "game_changer", False))


# Lands worth spending a Crop Rotation on that are not themselves on the Game
# Changers list. The listed ones are found via `is_game_changer`, so this only
# has to carry the rest, and the halves together are what makes the card what
# it is: Crop Rotation converts a land into *the* land, which needs the deck to
# hold one worth converting into.
_HIGH_IMPACT_LANDS = frozenset((
    "dark depths", "thespian's stage", "cabal coffers", "urborg, tomb of yawgmoth",
    "nykthos, shrine to nyx", "maze of ith", "bazaar of baghdad", "diamond valley",
    "academy ruins", "volrath's stronghold", "bojuka bog", "boseiju, who endures",
    "otawara, soaring city", "takenuma, abandoned mire", "war room", "reliquary tower",
    "high market", "phyrexian tower", "miren, the moaning well", "westvale abbey",
    "hall of the bandit lord", "inventors' fair", "deserted temple",
))


def _crop_rotation_lives(deck: list) -> bool:
    return any(
        "Land" in c.type_line
        and (is_game_changer(c) or c.name.lower() in _HIGH_IMPACT_LANDS)
        for c in deck
    )


# Game Changers whose power is a property of the deck around them rather than
# of the card. They stay in the official count either way — the list is WotC's
# and the bracket rule counts cards — but an inert one is worth saying out loud.
CONDITIONAL_GAME_CHANGERS = {
    "crop rotation": (
        _crop_rotation_lives,
        "there is no land in this deck worth fetching with it",
    ),
}


def inert_game_changers(cards: list) -> list:
    """Game Changers present that this deck gives nothing to do."""
    out = []
    for card in cards:
        rule = CONDITIONAL_GAME_CHANGERS.get(card.name.lower())
        if rule and not rule[0](cards):
            out.append((card.name, rule[1]))
    return out


def is_mass_land_denial(card) -> bool:
    t = _text(card)
    if _SPARES_LANDS.search(t):
        return False
    return bool(_MLD_RE.search(t) or _WIPE_INCLUDES_LANDS.search(t))


def is_extra_turn(card) -> bool:
    return bool(_EXTRA_TURN_RE.search(_text(card)))


def is_chainable_extra_turn(card) -> bool:
    """An extra-turn card that can keep taking them.

    A permanent that grants turns does so every upkeep; a sorcery grants one and
    is spent. Recurring a spent Time Warp is possible but takes a second card,
    which is a combo question rather than an extra-turn one.
    """
    if not is_extra_turn(card) or _SELF_EXILE_RE.search(_text(card)):
        return False
    return not ("Instant" in card.type_line or "Sorcery" in card.type_line)


def is_tutor(card) -> bool:
    """Library search for a nonland card. Land fetch is ramp, and does not count."""
    match = _TUTOR_RE.search(_text(card))
    return bool(match) and not _LAND_FETCH_RE.search(match.group(1))


# Words in a tutor's qualifier that say nothing about what it can find.
_TUTOR_NOISE = frozenset((
    "a", "an", "up", "to", "or", "and", "with", "less", "greater", "than",
    "mana", "value", "cost", "converted", "any", "that", "share", "type",
    "types", "name", "named", "the", "of", "in", "your", "you", "control",
    "other", "one", "two", "three", "four", "five", "nonlegendary", "legendary",
))


# "Search your library for a card named X" — the qualifier is empty, so the
# generic path reads these as unrestricted when they are the narrowest tutors
# there are. In a singleton format one named after itself finds nothing at all.
_NAMED_TUTOR_RE = re.compile(r"search your library for a card named ([^,.]+)")


def named_tutor_target(card) -> str:
    match = _NAMED_TUTOR_RE.search(_text(card))
    return match.group(1).strip() if match else ""


def tutor_restriction(card) -> list:
    """The card types a tutor is restricted to, as lowercase words.

    Empty for an unrestricted tutor: Demonic Tutor searches "for a card" and
    can always find something worth having.
    """
    match = _TUTOR_RE.search(_text(card))
    if not match:
        return []
    # Three letters minimum: no card type is shorter, and a one-letter scrap
    # substring-matches nearly every type line.
    return [w for w in re.findall(r"[a-z]+", match.group(1))
            if len(w) >= 3 and w not in _TUTOR_NOISE]


def is_live_tutor(card, deck: list) -> bool:
    """Whether this tutor can actually find anything in this deck.

    Honored Knight-Captain searches for an Equipment card. In a deck with no
    Equipment it is a six-mana sacrifice that shuffles the library — a tutor by
    rules text and nothing at all in play. Counting it overstates a deck's
    consistency, and players notice: it was the first thing a tester queried.

    Deliberately conservative — a tutor is dead only when *none* of its
    restriction words appear anywhere in the deck's type lines. A narrow tutor
    with a broad word in it (an Elf creature tutor in an Elf-less creature deck)
    still reads as live, because half-finding something is not nothing and a
    false accusation is worse here than a miss.
    """
    named = named_tutor_target(card)
    if named:
        # Singleton: a card that searches for its own name can never find one,
        # and one naming another card is live only if the deck runs it.
        return any(c.name.lower() == named and c is not card for c in deck)

    words = tutor_restriction(card)
    if not words:
        return True
    types = " ".join(c.type_line.lower() for c in deck if c is not card)
    return any(w in types for w in words)


def is_fast_mana(card) -> bool:
    return card.name.lower() in _FAST_MANA


def is_free_spell(card) -> bool:
    return bool(_FREE_SPELL_RE.search(_text(card)))


def is_cheap_answer(card) -> bool:
    """A one- or two-mana way to answer something, at instant speed."""
    return ("Instant" in card.type_line and card.cmc <= 2
            and bool(_INTERACTION_RE.search(_text(card))))


def _avg_mana_value(cards: list) -> float:
    spells = [c for c in cards if "Land" not in c.type_line]
    return sum(c.cmc for c in spells) / len(spells) if spells else 0.0


def _speed_signals(cards: list) -> list:
    """Ways this deck reads as able to end a game early.

    None of these are in the published rules — see the module docstring. Each is
    a whole-deck density rather than a single card, because one Mana Vault does
    not make a deck fast and eight one-mana answers do.
    """
    signals = []
    fast = [c.name for c in cards if is_fast_mana(c)]
    if len(fast) >= MIN_FAST_MANA_FAST:
        signals.append("fast mana: " + ", ".join(sorted(set(fast))))

    free = [c.name for c in cards if is_free_spell(c)]
    if len(free) >= MIN_FREE_SPELLS_FAST:
        signals.append("free spells: " + ", ".join(sorted(set(free))))

    answers = [c for c in cards if is_cheap_answer(c)]
    if len(answers) >= MIN_CHEAP_ANSWERS_FAST:
        signals.append(f"{len(answers)} one- or two-mana instant-speed answers")

    avg = _avg_mana_value(cards)
    if avg and avg <= MAX_AVG_MV_FAST:
        signals.append(f"average mana value {avg:.1f}")
    return signals


# ── Criteria ─────────────────────────────────────────────────────────────────

class Criterion(NamedTuple):
    """One bracket input, with the cards that produced it."""

    key: str
    label: str
    count: int
    cards: list      # card names, for the UI to list
    note: str        # what this count means for the bracket
    unchecked: bool = False
    chained: int = 0  # extra turns only: how many of them repeat


def _criteria(cards: list, combos) -> list:
    """Every bracket input, whether or not it fired."""
    game_changers = [c for c in cards if is_game_changer(c)]
    inert = inert_game_changers(cards)
    land_denial = [c for c in cards if is_mass_land_denial(c)]
    extra_turns = [c for c in cards if is_extra_turn(c)]
    chainable = [c for c in extra_turns if is_chainable_extra_turn(c)]
    # A tutor that can find nothing in this deck is not tutoring.
    all_tutors = [c for c in cards if is_tutor(c)]
    tutors = [c for c in all_tutors if is_live_tutor(c, cards)]
    dead_tutors = [c for c in all_tutors if c not in tutors]

    def names(group):
        return sorted({c.name for c in group})

    out = [
        Criterion(
            "game_changers", "Game Changers", len(game_changers), names(game_changers),
            "None — fine for any bracket." if not game_changers else
            ((f"{len(game_changers)} of the 3 a bracket 3 deck may run."
              if len(game_changers) <= MAX_GAME_CHANGERS_B3 else
              f"{len(game_changers)} is past the 3 a bracket 3 deck may run.")
             + (f" {_join([n for n, _ in inert])} still counts, but "
                f"{inert[0][1]}." if inert else "")),
        ),
        Criterion(
            "land_denial", "Mass land denial", len(land_denial), names(land_denial),
            "None." if not land_denial else
            "Brackets 1-3 don't allow it, so this deck is at least a 4.",
        ),
        Criterion(
            "extra_turns", "Extra turns", len(extra_turns), names(extra_turns),
            "None." if not extra_turns else
            (f"{len(chainable)} can be chained, which brackets 1-3 don't allow."
             if chainable else
             f"{len(extra_turns)} one-shot — past the low quantities brackets 2 "
             "and 3 expect." if len(extra_turns) > MAX_EXTRA_TURNS_B3 else
             f"{len(extra_turns)} one-shot, which brackets 2 and 3 allow."),
            chained=len(chainable),
        ),
        Criterion(
            "tutors", "Tutors", len(tutors), names(tutors),
            ("None." if not dead_tutors else
             f"None that can find anything — {_join(names(dead_tutors))} "
             f"searches for a card type this deck doesn't run.") if not tutors else
            "Tutors stopped restricting brackets in the October 2025 update; "
            "the efficient ones are Game Changers, and already counted above."
            + (f" {_join(names(dead_tutors))} isn't counted: there is nothing "
               f"in the deck for it to find." if dead_tutors else ""),
        ),
    ]

    if combos is None or not combos.checked:
        reason = (combos.error.split("\n")[0][:80] if combos and combos.error
                  else "the combo database wasn't reached")
        # The consequence lives in `notes`, which both the CLI and the web UI
        # print once; repeating it here read as duplicated copy in the panel.
        out.append(Criterion(
            "combos", "Two-card combos", 0, [],
            f"Not checked — {reason}.",
            unchecked=True,
        ))
    else:
        two = combos.two_card
        early = combos.early
        # Spellbook counts a combo whose third piece is described rather than
        # named ("a persist creature") even when the deck holds no such card.
        # Those are set aside in `combos.py`; saying so here is what stops the
        # missing count reading as a bug.
        missing = combos.unassembled
        wanted = sorted({m for c in missing for m in c["missing"]})
        one = len(missing) == 1
        aside = ("" if not missing else
                 f" {len(missing)} more "
                 + ("needs a card" if one else "need cards")
                 + f" this deck doesn't run ({_join(wanted)}), so "
                 + ("it isn't" if one else "they aren't") + " counted.")
        out.append(Criterion(
            "combos", "Two-card combos", len(two),
            [" + ".join(c["cards"]) for c in two],
            ("None." if not two else
             (f"{len(early)} of {len(two)} can go off before turn {EARLY_TURN}, "
              "which brackets 1-3 don't allow." if early else
              f"{len(two)}, all late-game — allowed from bracket 3 up.")) + aside,
        ))

    # Speed is context at every bracket, so this note stays bracket-neutral; the
    # comparison against a bracket's expected game length lives in `_notes`,
    # which knows which bracket the deck landed in.
    signals = _speed_signals(cards)
    out.append(Criterion(
        "speed", "Deck speed", len(signals), signals,
        "Nothing here rushes a game." if not signals else
        "One sign of speed, which any bracket carries fine."
        if len(signals) < SPEED_SIGNALS_FAST else
        "Built to close games early, whatever the restrictions allow.",
    ))
    return out


def _bracket_from(criteria: dict, combos) -> tuple[int, list]:
    """Lowest bracket the deck fits, plus the reasons it cannot go lower.

    The floor is 2, never 1: see the module docstring — bracket 1 is a claim
    about intent, and a deck that satisfies its restrictions is usually just an
    ordinary casual deck.
    """
    game_changers = criteria["game_changers"].count
    land_denial = criteria["land_denial"].count
    extra_turns = criteria["extra_turns"].count
    chained_turns = criteria["extra_turns"].chained

    two_card = combos.two_card if (combos and combos.checked) else []
    early_combo = combos.early if (combos and combos.checked) else []

    blockers: list[str] = []

    # Bracket 4 is the floor for these: nothing below it permits them at all.
    if land_denial:
        blockers.append("mass land denial")
    if game_changers > MAX_GAME_CHANGERS_B3:
        blockers.append(f"{game_changers} Game Changers")
    if chained_turns:
        blockers.append("repeatable extra turns")
    elif extra_turns > MAX_EXTRA_TURNS_B3:
        # Brackets 2 and 3 ask for extra turns "in low quantities"; a stack of
        # them is a plan to keep taking turns even when no single card loops.
        blockers.append(f"{extra_turns} extra-turn cards")
    if early_combo:
        blockers.append("an early two-card combo")
    if blockers:
        return 4, blockers

    # Bracket 3 admits a few Game Changers and a late combo; 2 admits neither.
    if game_changers:
        blockers.append(f"{game_changers} Game Changer" + ("s" if game_changers > 1 else ""))
    if two_card:
        blockers.append("a two-card infinite combo")
    if blockers:
        return 3, blockers

    return 2, []


def _exhibition_ok(criteria: dict, combos) -> bool:
    """Whether the deck also clears bracket 1's stricter rules.

    Only ever reported as an option — whether a deck *is* an Exhibition deck is
    a question about how it was built, which its pilot answers.
    """
    return (not criteria["extra_turns"].count
            and bool(combos and combos.checked)
            and not combos.two_card)


def _apply_crispi_floor(number: int, blockers: list, crispi: dict | None) -> tuple[int, list, dict | None]:
    """Raise `number` to a CRISPI floor when the deck trips one.

    The published restrictions cannot see how fast a deck actually plays, so
    DeckCheck's guardrails catch a deck that obeys every rule of its bracket
    and still ends games earlier than that bracket expects. They only ever bump
    *up*: nobody objects to a weak deck sitting in a high bracket, and two of
    the four inputs are estimated here, so a bump down could be simply wrong.
    """
    floor = (crispi or {}).get("floor")
    if not floor or floor["bracket"] <= number:
        return number, blockers, None
    applied = {"from": number, "to": floor["bracket"], "reason": floor["reason"]}
    return floor["bracket"], blockers + [f"a CRISPI floor ({floor['reason']})"], applied


def evaluate(commander, deck: list, combos=None, *, fmt: str = "commander",
             target: int | None = None, crispi: dict | None = None) -> dict:
    """Estimate the bracket of `deck`, with the evidence behind it.

    `combos` is a `combos.ComboResult`, or None when the lookup was skipped;
    either way the criterion is reported as unchecked rather than as clean.

    `target` is the bracket the deck was *built* for, when it was built for one.
    It changes nothing about the estimate — it only adds a `target` block saying
    whether the build got there, so the CLI and the web UI say the same thing.

    `crispi` is a `crispi.evaluate` result. Its floors can raise the bracket
    above what the card restrictions alone give, never lower it.
    """
    cards = [c for c in deck if not c.is_basic_filler]
    if commander is not None:
        cards = [commander] + cards

    criteria = _criteria(cards, combos)
    by_key = {c.key: c for c in criteria}
    number, blockers = _bracket_from(by_key, combos)
    number, blockers, crispi_bump = _apply_crispi_floor(number, blockers, crispi)
    # Worth calling out only when it is the whole reason for the bracket: an
    # inert Game Changer alongside three live ones is not what put a deck at 3.
    inert = inert_game_changers(cards)
    inert_decides = bool(inert) and by_key["game_changers"].count == len(inert)
    fast = by_key["speed"].count >= SPEED_SIGNALS_FAST
    # A deck built to close games early is not an Exhibition deck under any
    # reading, so the two notes are never offered together.
    exhibition_ok = number == 2 and not fast and _exhibition_ok(by_key, combos)

    # `reason` stands alone for callers that have already named the bracket
    # (the CLI block, the scale in the web UI); `headline` is the full sentence.
    if blockers:
        reason = "held there by " + _join(blockers)
    elif fast:
        reason = ("no Game Changers, no land denial and no combos, but built to "
                  f"win before the turn {BRACKET_TURNS[2]} this bracket expects")
    else:
        reason = ("no Game Changers, no land denial, no combos — the classic "
                  f"casual game Core (2) describes, decided on turn "
                  f"{BRACKET_TURNS[2]} or later")
    headline = f"{BRACKET_NAMES[number]} ({number}) — {reason}."

    unchecked = [c for c in criteria if c.unchecked]
    result = {
        "number": number,
        "name": BRACKET_NAMES[number],
        "label": f"{BRACKET_NAMES[number]} ({number})",
        "blurb": BRACKET_BLURBS[number],
        "headline": headline,
        "reason": reason,
        "criteria": [c._asdict() for c in criteria],
        "combos": {
            "checked": bool(combos and combos.checked),
            "two_card": (combos.two_card if combos and combos.checked else []),
            "other": (combos.other if combos and combos.checked else [])[:5],
            "unassembled": (combos.unassembled
                            if combos and combos.checked else [])[:5],
        },
        # A deck can always be played *up*, and brackets 1 and 5 are statements
        # of intent that no decklist can settle.
        "exhibition_ok": exhibition_ok,
        "fast_for_bracket": fast,
        "crispi": crispi,
        "crispi_bump": crispi_bump,
        "notes": _notes(number, unchecked, fmt, exhibition_ok=exhibition_ok,
                        fast=fast, crispi_bump=crispi_bump,
                        inert=inert if inert_decides else []),
    }
    result["target"] = _target_outcome(target, result) if target else None
    return result


def _notes(number: int, unchecked: list, fmt: str, *,
           exhibition_ok: bool = False, fast: bool = False,
           crispi_bump: dict | None = None, inert: list | None = None) -> list:
    notes = []
    if inert:
        names = _join([n for n, _ in inert])
        notes.append(
            f"{names} is the only thing holding this deck above bracket 2, and "
            f"{inert[0][1]} — so it is a Game Changer on the list without being "
            f"one at the table. The count is WotC's rule and every other tool "
            f"will read it the same way, so {number} is the honest number to "
            f"give a table; just say what it is doing there."
        )
    if unchecked:
        notes.append(
            "Two-card combos weren't checked, so the real bracket may be higher."
        )
    if crispi_bump:
        notes.append(
            f"The card restrictions put this at {crispi_bump['from']}, but its "
            f"CRISPI score trips the bracket {crispi_bump['to']} floor "
            f"({crispi_bump['reason']}) — it plays faster than bracket "
            f"{crispi_bump['from']} expects. Speed and Resilience are estimated "
            f"rather than counted, so treat the bump as a prompt to check the "
            f"deck against the table, not as a verdict."
        )
    if fast and number == 2:
        notes.append(
            f"The restrictions put this at 2, but bracket 2 expects games to "
            f"reach turn {BRACKET_TURNS[2]} and this deck is built to end one "
            f"sooner — pitch it as a 3 (turn {BRACKET_TURNS[3]}+) and nobody at "
            "the table will be surprised."
        )
    if exhibition_ok:
        notes.append(
            "It clears bracket 1's stricter rules too, but Exhibition is for a "
            "deck whose theme was chosen ahead of its power, with games running "
            f"{BRACKET_TURNS[1]} turns or more — call it a 1 only if that's what "
            "it's for. Bracket 2 is where an ordinary casual deck belongs."
        )
    if number == 4:
        notes.append(
            "Bracket 5 (cEDH) isn't assigned from a decklist — it means the deck "
            "is built for a tournament metagame, which only you can say."
        )
    if fmt != "commander":
        notes.append(
            "Brackets are a Commander framework; this is that scale applied to "
            f"{fmt.title()}, which has its own card pool and ban list."
        )
    return notes


def _target_outcome(target: int, result: dict) -> dict:
    """Whether a build aimed at bracket `target` got what it asked for.

    The builder controls the card-level criteria, so it either hits the target
    or is stopped by something it could not put in the deck — a Game Changer
    the collection does not hold — or by something it could not see coming,
    which in practice means a two-card combo.
    """
    got = result["number"]
    crit = {c["key"]: c for c in result["criteria"]}
    unchecked = crit["combos"]["unchecked"]
    caveat = ("" if not unchecked else
              " Two-card combos weren't checked, so this is the card-level"
              " answer only.")

    if target == 1:
        # We floor at 2, so a bracket 1 request can only ever be honoured as a
        # constraint on what went in — the label itself is the pilot's to claim.
        # That constraint is card-level, so it holds whether or not the combo
        # lookup ran, unlike `exhibition_ok`.
        if got == 2 and not crit["extra_turns"]["count"] and not crit["combos"]["count"]:
            return _outcome(target, True,
                            "Built inside Exhibition's restrictions — no Game Changers, "
                            "no land denial, no extra turns, no combos. It is reported as "
                            "2 because Exhibition is a claim about why a deck was built, "
                            "which a decklist cannot make for you." + caveat)
        return _outcome(target, False,
                        f"Missed — the deck came out as {result['label']}, "
                        f"{result['reason']}.")

    if got == target:
        return _outcome(target, True, f"Met — {result['reason']}.{caveat}")

    if got > target:
        return _outcome(target, False,
                        f"Missed — the deck came out as {result['label']}, "
                        f"{result['reason']}. The builder keeps out what a bracket "
                        f"disallows card by card, but two-card combos only show up once "
                        f"the pairs are together, so they can still push a deck past "
                        f"its target.")

    # got < target: the collection had nothing left to climb with. Every deck
    # is welcome at a table above its own bracket, so this is not an error.
    gc = crit["game_changers"]["count"]
    owned_gc = f"{gc} Game Changer" + ("" if gc == 1 else "s")
    if target == 3:
        why = (f"it runs {owned_gc}, and "
               + ("the combo database wasn't reached" if unchecked else
                  "no two-card combo turned up")
               + " — bracket 3 needs one or the other")
    else:
        tier = "bracket 5 is" if target == 5 else f"brackets {target} and up are"
        why = (f"it runs {owned_gc} and nothing else in these colours pushes it "
               f"higher; {tier} reached by playing stronger cards than the "
               f"collection holds")
    return _outcome(target, False,
                    f"Not reached — the deck is {result['label']}, because {why}. "
                    f"Play it a bracket up if the table wants to.")


def _outcome(target: int, met: bool, report: str) -> dict:
    return {
        "number": target,
        "name": BRACKET_NAMES[target],
        "met": met,
        "report": report,
        # The one-line form the CLI prints, and the UI's accessible label.
        "line": f"Target bracket {target} ({BRACKET_NAMES[target]}): "
                f"{report[0].lower()}{report[1:]}",
    }


def _join(items: list) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def summary_line(bracket: dict) -> str:
    """One-line form for the CLI and MCP output."""
    return f"Bracket {bracket['number']} ({bracket['name']}) — {bracket['reason']}."
