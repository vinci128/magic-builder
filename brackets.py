"""Commander bracket (1-5) estimation for a built singleton deck.

The bracket system is WotC's shared vocabulary for how hard a Commander deck is
trying. Each bracket publishes what a deck may contain, so this module tests
those restrictions and reports the lowest bracket the deck still fits:

    1 Exhibition  no Game Changers, no mass land denial, no extra-turn cards,
                  no two-card infinite combos
    2 Core        as above, but a few extra-turn cards are fine so long as they
                  are not chained
    3 Upgraded    up to 3 Game Changers, no mass land denial, no chained extra
                  turns, and any two-card combo must be a late-game one
    4 Optimized   no restrictions
    5 cEDH        no restrictions, plus intent to compete in a tournament meta

Why the floor is 2, not 1
-------------------------
Two of the five brackets are never assigned here, for the same reason: they say
*why* a deck was built, which no decklist shows.

Bracket 5 is the obvious one — only a tournament mindset separates it from 4.
Bracket 1 is the subtler one. Exhibition is not "a deck that happens to break
none of the rules": WotC describes it as ultra-casual, theme chosen before
power, with games expected to run nine turns or more. Every preconstructed deck
clears bracket 1's restriction list — precons carry no Game Changers, no land
denial, no extra turns and no combos — and yet WotC's own yardstick is that the
average precon is a Core (2) deck. So the restrictions cannot tell 1 from 2, and
a classifier that stops at the first list a deck satisfies labels every clean
deck an Exhibition deck, which is how three bracket-2 precons all came out as 1.

The floor is therefore 2. When a deck also clears bracket 1's stricter rules
that is reported as a note the pilot may claim, not as a verdict.

Above the precon baseline
-------------------------
The same "lowest list that fits" rule under-reads decks the other way: a deck
with fast mana and a pile of one-mana answers but no Game Changer is a 2 by the
letter of the rules, and plainly not a precon-level deck at a table.
`_tuning_signals` looks for that, and adds an advisory criterion. It does not
move the number — the number stays the published, checkable one.

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
counts as above-precon tuning) are this module's own reading — WotC gives those
limits in words, not numbers.
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
    1: "Ultra-casual: a theme deck built to show off, with games running 9+ turns.",
    2: "The classic casual Commander experience — a real strategy winning around turn 8 or later.",
    3: "Upgraded: tuned past precon strength, with a late-game plan to win.",
    4: "Optimized: the strongest build of its idea, short of tournament play.",
    5: "cEDH: built to beat the tournament metagame, not just to be powerful.",
}

MAX_GAME_CHANGERS_B3 = 3   # published: bracket 3 allows up to three
MAX_EXTRA_TURNS_B3 = 3     # our reading of "low quantities" — more is a plan
TUNING_SIGNALS_ABOVE_B2 = 2  # how many tuning signals read as "past precon"


# ── Card-level detectors ─────────────────────────────────────────────────────

# Sweeps and locks that take everyone's lands away. Spot land removal
# ("destroy target land") is deliberately absent: one Ghost Quarter is not a
# land-denial plan, and the bracket rules are about the plan.
# The land clause is matched inside the sweep it belongs to, so a wipe that
# takes lands along with everything else counts (Jokulhaups: "destroy all
# artifacts, creatures, and lands"), while one that spares them does not
# ("destroy all nonland permanents" — `\blands\b` will not match "nonland").
_MLD_PATTERNS = (
    r"(destroy|exile|return) all [^.]{0,40}\blands\b",
    r"each (player|opponent) sacrifices \w+ lands?",
    r"\blands don't untap",
    r"permanents don't untap",
)
_MLD_RE = re.compile("|".join(_MLD_PATTERNS))

# "Destroy all permanents" (Obliterate) names no land but takes every one.
_WIPE_INCLUDES_LANDS = re.compile(r"(destroy|exile) all permanents")

_EXTRA_TURN_RE = re.compile(r"takes? an extra turn|take an extra turn after this one")

# An extra-turn spell that exiles itself on resolution cannot be chained, which
# is the thing brackets 2 and 3 actually forbid.
_SELF_EXILE_RE = re.compile(r"exile (it|this card)(\.| instead)")

# Tutoring: fetching *a card* out of the library, however the search is worded —
# "for a card", "for a creature card", "and graveyard for an artifact card". The
# qualifier between "for a" and "card" is captured so land fetch can be dropped:
# every ramp deck searches up a Forest and the bracket rules do not count it.
_TUTOR_RE = re.compile(
    r"search your library[^.]{0,25}?for (?:a|an|up to \w+)([^.,;]{0,40}?)\bcards?\b"
)
_LAND_FETCH_RE = re.compile(
    r"\b(land|lands|basic|plains|island|swamp|mountain|forest|gate)\b"
)

# ── Tuning signals (not part of the published rules; see the module docstring) ─

# Fast mana that a precon would not ship. Sol Ring is deliberately absent: it is
# in nearly every precon, so it says nothing about a deck being tuned past one.
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

# One- and two-mana answers. Density of these is what separates a tuned deck
# from a precon far more reliably than any single card does.
_INTERACTION_RE = re.compile(
    r"counter target|destroy target|exile target|"
    r"sacrifices? a (creature|permanent)|return target .{0,20}to its owner's hand"
)

MIN_FAST_MANA_TUNED = 2
MIN_FREE_SPELLS_TUNED = 2
MIN_CHEAP_ANSWERS_TUNED = 8
MAX_AVG_MV_TUNED = 2.6      # precons sit near 3.3


def _text(card) -> str:
    return (card.oracle_text or "").lower()


def is_game_changer(card) -> bool:
    return bool(getattr(card, "game_changer", False))


def is_mass_land_denial(card) -> bool:
    t = _text(card)
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


def _tuning_signals(cards: list) -> list:
    """Ways this deck reads as tuned past the precon baseline that defines a 2.

    None of these are in the published rules — see the module docstring. Each is
    a whole-deck density rather than a single card, because one Mana Vault does
    not make a deck strong and eight one-mana answers do.
    """
    signals = []
    fast = [c.name for c in cards if is_fast_mana(c)]
    if len(fast) >= MIN_FAST_MANA_TUNED:
        signals.append("fast mana: " + ", ".join(sorted(set(fast))))

    free = [c.name for c in cards if is_free_spell(c)]
    if len(free) >= MIN_FREE_SPELLS_TUNED:
        signals.append("free spells: " + ", ".join(sorted(set(free))))

    answers = [c for c in cards if is_cheap_answer(c)]
    if len(answers) >= MIN_CHEAP_ANSWERS_TUNED:
        signals.append(f"{len(answers)} one- or two-mana instant-speed answers")

    avg = _avg_mana_value(cards)
    if avg and avg <= MAX_AVG_MV_TUNED:
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
    land_denial = [c for c in cards if is_mass_land_denial(c)]
    extra_turns = [c for c in cards if is_extra_turn(c)]
    chainable = [c for c in extra_turns if is_chainable_extra_turn(c)]
    tutors = [c for c in cards if is_tutor(c)]

    def names(group):
        return sorted({c.name for c in group})

    out = [
        Criterion(
            "game_changers", "Game Changers", len(game_changers), names(game_changers),
            "None — fine for any bracket." if not game_changers else
            (f"{len(game_changers)} of the 3 a bracket 3 deck may run."
             if len(game_changers) <= MAX_GAME_CHANGERS_B3 else
             f"{len(game_changers)} is past the 3 a bracket 3 deck may run."),
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
            "None." if not tutors else
            f"{len(tutors)} — tutors stopped restricting brackets in the "
            "October 2025 update; the efficient ones are Game Changers "
            "and already counted above.",
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
        out.append(Criterion(
            "combos", "Two-card combos", len(two),
            [" + ".join(c["cards"]) for c in two],
            "None." if not two else
            (f"{len(early)} of {len(two)} can go off before turn {EARLY_TURN}, "
             "which brackets 1-3 don't allow." if early else
             f"{len(two)}, all late-game — allowed from bracket 3 up."),
        ))

    signals = _tuning_signals(cards)
    out.append(Criterion(
        "tuning", "Above precon baseline", len(signals), signals,
        "None — this plays at the precon level bracket 2 describes."
        if not signals else
        "One sign of tuning, which a bracket 2 deck can carry."
        if len(signals) < TUNING_SIGNALS_ABOVE_B2 else
        "Reads as tuned past precon strength, whatever the restrictions allow.",
    ))
    return out


def _bracket_from(criteria: dict, combos) -> tuple[int, list]:
    """Lowest bracket the deck fits, plus the reasons it cannot go lower.

    The floor is 2, never 1: see the module docstring — bracket 1 is a claim
    about intent, and a deck that satisfies its restrictions is usually just a
    precon-level deck.
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


def evaluate(commander, deck: list, combos=None, *, fmt: str = "commander") -> dict:
    """Estimate the bracket of `deck`, with the evidence behind it.

    `combos` is a `combos.ComboResult`, or None when the lookup was skipped;
    either way the criterion is reported as unchecked rather than as clean.
    """
    cards = [c for c in deck if not c.is_basic_filler]
    if commander is not None:
        cards = [commander] + cards

    criteria = _criteria(cards, combos)
    by_key = {c.key: c for c in criteria}
    number, blockers = _bracket_from(by_key, combos)
    tuned = by_key["tuning"].count >= TUNING_SIGNALS_ABOVE_B2
    exhibition_ok = number == 2 and _exhibition_ok(by_key, combos)

    # `reason` stands alone for callers that have already named the bracket
    # (the CLI block, the scale in the web UI); `headline` is the full sentence.
    if blockers:
        reason = "held there by " + _join(blockers)
    elif tuned:
        reason = ("no Game Changers, no land denial and no combos, but it is "
                  "built well past precon strength")
    else:
        reason = ("no Game Changers, no land denial, no combos — the classic "
                  "casual Commander level Core (2) describes, where most "
                  "precons play")
    headline = f"{BRACKET_NAMES[number]} ({number}) — {reason}."

    unchecked = [c for c in criteria if c.unchecked]
    return {
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
        },
        # A deck can always be played *up*, and brackets 1 and 5 are statements
        # of intent that no decklist can settle.
        "exhibition_ok": exhibition_ok,
        "tuned_past_precon": tuned,
        "notes": _notes(number, unchecked, fmt,
                        exhibition_ok=exhibition_ok, tuned=tuned),
    }


def _notes(number: int, unchecked: list, fmt: str, *,
           exhibition_ok: bool = False, tuned: bool = False) -> list:
    notes = []
    if unchecked:
        notes.append(
            "Two-card combos weren't checked, so the real bracket may be higher."
        )
    if tuned and number == 2:
        notes.append(
            "The restrictions put this at 2, but it plays above the precon "
            "baseline — pitch it as a 3 and nobody at the table will be surprised."
        )
    if exhibition_ok:
        notes.append(
            "It clears bracket 1's stricter rules too, but Exhibition means a "
            "deck built around a theme rather than winning, with games running "
            "nine turns or more — call it a 1 only if that's what it's for. "
            "The average precon is a 2, and so is this."
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


def _join(items: list) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def summary_line(bracket: dict) -> str:
    """One-line form for the CLI and MCP output."""
    return f"Bracket {bracket['number']} ({bracket['name']}) — {bracket['reason']}."
