"""Commander bracket (1-5) estimation for a built singleton deck.

The bracket system is WotC's shared vocabulary for how hard a Commander deck is
trying. A deck's bracket is the *lowest* one whose restrictions it satisfies, so
this module tests the restrictions in order and stops at the first fit:

    1 Exhibition  no Game Changers, no mass land denial, no extra turns,
                  no two-card infinite combos, and essentially no tutoring
    2 Core        as above, but a stray extra turn and a few tutors are fine
    3 Upgraded    up to 3 Game Changers, still no land denial or chained extra
                  turns, and any two-card combo must be a late-game one
    4 Optimized   no restrictions
    5 cEDH        no restrictions, plus intent to compete in a tournament meta

Bracket 5 is never assigned here: nothing separates it from 4 in a decklist —
it is a statement about why the deck was built, which only its pilot knows.

Where the numbers come from
---------------------------
Game Changers are a published list, read off Scryfall's `game_changer` field,
so that criterion is exact. Mass land denial, extra turns and tutors are
inferred from rules text, in the style of `deck_builder.py`'s role detectors:
good enough to sort decks, not a rules oracle. Two-card combos come from
Commander Spellbook via `combos.py`, and are reported as unchecked rather than
as absent when that lookup could not run.

The tutor thresholds are this module's own reading. WotC says bracket 1 runs no
tutors and bracket 2 "a small number", without numbers, so tutoring only ever
decides 1-vs-2 here and never pushes a deck to 3 or 4 on its own.
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
    1: "Ultra-casual: a theme deck built to show off, not to win fast.",
    2: "The precon baseline — a deck that wins around turn 9 or later.",
    3: "Upgraded: tuned, with a few strong cards and a late-game plan to win.",
    4: "Optimized: the strongest build of its idea, short of tournament play.",
    5: "cEDH: built to beat the tournament metagame, not just to be powerful.",
}

MAX_GAME_CHANGERS_B3 = 3   # published: bracket 3 allows up to three
MAX_TUTORS_B1 = 1          # our reading of "no tutors" — one stray is not a plan
MAX_TUTORS_B2 = 3          # our reading of "a small number of tutors"


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

# Unrestricted tutoring: fetching *a card*, or a specific spell type, to hand or
# battlefield. Land fetch is excluded — every ramp deck does it and the bracket
# rules do not count it.
_TUTOR_RE = re.compile(
    r"search your library for (a|an|up to \w+) "
    r"(card|creature card|artifact card|enchantment card|instant card|"
    r"sorcery card|planeswalker card|permanent card)"
)


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
    """Unrestricted library search. Land fetch is ramp, and `_TUTOR_RE` skips it."""
    return bool(_TUTOR_RE.search(_text(card)))


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
            (f"{len(extra_turns)} one-shot, which brackets 2 and 3 allow."
             if not chainable else
             f"{len(chainable)} can be chained, which brackets 1-3 don't allow."),
            chained=len(chainable),
        ),
        Criterion(
            "tutors", "Tutors", len(tutors), names(tutors),
            "None." if not tutors else
            (f"{len(tutors)} — few enough for a bracket 2 deck."
             if len(tutors) <= MAX_TUTORS_B2 else
             f"{len(tutors)} is more tutoring than a bracket 2 deck expects."),
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
    return out


def _bracket_from(criteria: dict, combos) -> tuple[int, list]:
    """Lowest bracket the deck fits, plus the reasons it cannot go lower."""
    game_changers = criteria["game_changers"].count
    land_denial = criteria["land_denial"].count
    extra_turns = criteria["extra_turns"].count
    chained_turns = criteria["extra_turns"].chained
    tutors = criteria["tutors"].count

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
    if early_combo:
        blockers.append("an early two-card combo")
    if blockers:
        return 4, blockers

    # Bracket 3 admits a few Game Changers and a late combo; 1 and 2 admit neither.
    if game_changers:
        blockers.append(f"{game_changers} Game Changer" + ("s" if game_changers > 1 else ""))
    if two_card:
        blockers.append("a two-card infinite combo")
    if blockers:
        return 3, blockers

    # Between 1 and 2 it is about restraint: no extra turns and no tutoring.
    if extra_turns:
        blockers.append("an extra-turn card")
    if tutors > MAX_TUTORS_B1:
        blockers.append(f"{tutors} tutors")
    if blockers:
        return 2, blockers

    return 1, []


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

    # `reason` stands alone for callers that have already named the bracket
    # (the CLI block, the scale in the web UI); `headline` is the full sentence.
    if blockers:
        reason = "held there by " + _join(blockers)
        headline = f"{BRACKET_NAMES[number]} ({number}) — {reason}."
    else:
        reason = ("no Game Changers, no land denial, no extra turns, "
                  "no combos — nothing pushes it higher")
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
        # A deck can always be played *up*, and bracket 5 is a statement of
        # intent that no decklist can settle.
        "notes": _notes(number, unchecked, fmt),
    }


def _notes(number: int, unchecked: list, fmt: str) -> list:
    notes = []
    if unchecked:
        notes.append(
            "Two-card combos weren't checked, so the real bracket may be higher."
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
