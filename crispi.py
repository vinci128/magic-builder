"""CRISPI scoring — Consistency, Resilience, Interaction, Speed.

DeckCheck's replacement for power levels, reimplemented from their published
rubric (`deckcheck.co/blog/crispi-deep-dive`, engine rewrite July 2026). Each
attribute scores 1-10 in quarter-point steps; the CRISPI Score is their mean,
snapped to the same grid. Its use here is `brackets.py`'s guardrails: a deck can
satisfy every published card restriction and still play far above the bracket
those restrictions put it in, and CRISPI is what catches that.

What is counted and what is guessed
-----------------------------------
DeckCheck's own engine is deterministic for Consistency and Interaction and
leaves exactly two judgements to an AI: Speed's *fundamental turn* and
Resilience's *commander dependency*. This module has no AI at deck-build time,
so those two attributes are **estimated** and carry `estimated=True` all the way
to the UI, which labels them. Read them as informed guesses, not as counts:

    Consistency  counted — tutor and draw point ladders, per the rubric
    Interaction  counted — piece count, stack/timing points, scope coverage
    Speed        estimated — from combo speed where Spellbook found one, else
                 from curve, ramp and fast mana. The rubric wants a dozen
                 goldfish hands played out; nothing here plays a hand.
    Resilience   estimated — combo layering and recursion points are counted,
                 but commander dependency is assumed to be the format default
                 ("Moderate", -1) because judging it needs a human read.

Two consequences worth keeping in mind. The estimates feed the CRISPI mean, so
the mean inherits their error; and the mean drives bracket floors, so a wrong
Speed can move a bracket. The floors only ever bump a deck *up* — that is
DeckCheck's rule, not a softening of ours — so the failure mode is a deck told
it plays stronger than it does, never one told it is weaker.

Where our numbers differ
------------------------
DeckCheck states the bracket turn expectations as 10/9/7/5. WotC's October 2025
update says 9/8/6/4, which is what `brackets.py` uses. We keep DeckCheck's floor
*thresholds* exactly as published, since those were calibrated against their
turn reading, and leave our own turn copy alone.

Our scale also runs low against theirs. DeckCheck recalibrated in July 2026 so
that precons centre on a CRISPI of about 5; the precons measured here land near
3.75, because their scoring leans on a hand-curated card database — which cards
are premium tutors, which draw is a real engine — and ours reads rules text.
Rather than multiply the numbers up to hit a centre we cannot verify, the scale
is left where the counting puts it. The direction of the error is the safe one:
scoring low means the floors fire *less* often than DeckCheck's would, so this
module under-promotes rather than over-promotes, and a bump you do see is one
the deck earned on the strict reading. Treat the attribute profile as the useful
output and the absolute number as this module's own scale, not DeckCheck's.
"""

import math
import re

import brackets

# ── Scoring helpers ──────────────────────────────────────────────────────────

MIN_SCORE, MAX_SCORE = 1.0, 10.0


def snap(value: float) -> float:
    """Round to the quarter-point grid, exact midpoints rounding up.

    `floor(x + 0.5)` rather than `round`, because Python rounds halves to even:
    round(6.125 * 4) is 24 and would read 6.0 where the rubric wants 6.25.
    """
    value = max(MIN_SCORE, min(MAX_SCORE, value))
    return math.floor(value * 4 + 0.5) / 4


def ladder(anchors: list, total: float) -> float:
    """Read `total` off a point ladder, interpolating between anchors.

    `anchors` is [(points, score), ...] ascending. A total on an anchor reads
    that score; totals between anchors interpolate linearly; past the last
    anchor reads the last score.
    """
    if total <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if total <= x1:
            span = x1 - x0
            return y0 if span == 0 else y0 + (y1 - y0) * (total - x0) / span
    return anchors[-1][1]


# ── Named card tiers ─────────────────────────────────────────────────────────
# The rubric names these explicitly; rules text cannot tell a Rhystic Study
# from any other enchantment, so the premium tiers are lists.

PREMIUM_TUTORS = frozenset((
    "demonic tutor", "vampiric tutor", "imperial seal", "enlightened tutor",
    "mystical tutor", "worldly tutor", "gamble", "grim tutor", "diabolic intent",
    "sylvan tutor", "personal tutor", "merchant scroll", "muddle the mixture",
))

REPEATABLE_TUTOR_ENGINES = frozenset((
    "survival of the fittest", "birthing pod", "fauna shaman", "prime speaker vannifar",
    "sisay, weatherlight captain", "captain sisay", "recruiter of the guard",
))

COMBAT_TUTOR_ENGINES = frozenset((
    "zur the enchanter", "armored skyhunter", "winota, joiner of forces",
    "gishath, sun's avatar",
))

COMBO_ENABLER_TUTORS = frozenset((
    "demonic consultation", "tainted pact", "doomsday", "spoils of the vault",
))

BURST_DRAW = frozenset((
    "ad nauseam", "wheel of fortune", "necropotence", "windfall", "timetwister",
    "echo of eons", "time spiral", "wheel of misfortune", "peer into the abyss",
    "pull from tomorrow", "sign in blood",  # only the big ones matter; see _draw_points
))

PREMIUM_DRAW_ENGINES = frozenset((
    "rhystic study", "mystic remora", "esper sentinel", "sylvan library",
    "consecrated sphinx", "smothering tithe", "trouble in pairs",
))

# Punishers — the rubric routes these to Interaction and explicitly out of draw.
PUNISHERS = frozenset((
    "sheoldred, the apocalypse", "orcish bowmasters", "underworld dreams",
    "hullbreacher", "notion thief", "narset, parter of veils", "aven mindcensor",
    "opposition agent", "drannith magistrate", "grand arbiter augustin iv",
))

EFFECTIVE_COUNTERS = frozenset((
    "veil of summer", "autumn's veil", "imp's mischief", "bolt bend",
    "deflecting swat", "pyroblast", "red elemental blast", "blue elemental blast",
    "hydroblast", "ricochet trap", "withering boon",
))

TURN_PROTECTION = frozenset((
    "silence", "grand abolisher", "orim's chant", "abeyance", "conqueror's flail",
    "teferi, time raveler", "dovin's veto",
))

BOARD_LEVEL_PROTECTION = frozenset((
    "teferi's protection", "heroic intervention", "boros charm", "flawless maneuver",
    "unbreakable formation", "make a stand", "rootborn defenses", "ghostway",
    "eerie interlude", "semester's end", "clever concealment", "fog", "holy day",
))

# ── Shared text patterns ─────────────────────────────────────────────────────

_COUNTER_RE = re.compile(r"counter target (spell|.{0,25}spell)")
_REMOVAL_RE = re.compile(
    r"destroy target|exile target (creature|artifact|enchantment|permanent|player|nonland)|"
    r"target creature gets [-−]|deals? \d+ damage to target (creature|any target)|"
    r"target player sacrifices|each opponent sacrifices|"
    r"gain control of target|target player discards"
)
# "Return all nonland permanents" (Cyclonic Rift, Evacuation) sweeps a board as
# surely as destroying it — bounce is only tempo when it is aimed at one thing.
_WIPE_RE = re.compile(
    r"(destroy|exile|return) all (creatures|nonland permanents|permanents|"
    r"artifacts|enchantments)|each player sacrifices|all creatures get [-−]"
)
_PREMIUM_WIPE_RE = re.compile(
    r"exile all (creatures|nonland permanents|permanents)|"
    r"each player sacrifices|all creatures get [-−]"
)
_GY_HATE_RE = re.compile(r"exile (all|target player's|each) .{0,20}graveyard|graveyards? .{0,15}exile")
_PROTECTION_RE = re.compile(
    r"gains? (hexproof|indestructible|protection)|"
    r"creatures you control gain|has hexproof|phases? out|"
    r"can't be (countered|targeted)"
)
_DETERRENT_RE = re.compile(r"creatures can't attack you unless|whenever a creature attacks you")
_RECURSION_RE = re.compile(
    r"return .{0,40}from your graveyard to (the battlefield|your hand)|"
    r"return target .{0,30}card from .{0,15}graveyard"
)
_SELECTION_RE = re.compile(r"\bscry\b|\bsurveil\b|look at the top \w+ cards?|\bconnive\b")
# The subject is captured so an opponent's draw does not read as your draw
# engine — "each player draws a card" is symmetric, "target opponent draws"
# is a downside, and neither is a source for you.
_DRAW_CLAUSE_RE = re.compile(r"([a-z' ]{0,22})draws? (a|an|\w+) cards?")
_NOT_YOUR_DRAW = ("opponent", "opponents", "player", "players")


def _draws_for_you(text: str) -> bool:
    for match in _DRAW_CLAUSE_RE.finditer(text):
        if not match.group(1).strip().endswith(_NOT_YOUR_DRAW):
            return True
    return False
_REPEATABLE_DRAW_RE = re.compile(r"at the beginning of|whenever")
_CHEAT_RE = re.compile(
    r"put .{0,40}(creature|permanent) card .{0,25}onto the battlefield|"
    r"return target creature card from your graveyard to the battlefield|"
    r"you may cast .{0,30}without paying"
)
_STAX_RE = re.compile(
    r"spells cost \{?\d?\}? ?more|players can't|can't untap|"
    r"don't untap during|skip (their|your) .{0,12}step|opponents can't"
)


def _text(card) -> str:
    return (card.oracle_text or "").lower()


def _name(card) -> str:
    return (card.name or "").lower()


def _is_instant_speed(card) -> bool:
    return "Instant" in card.type_line or "Flash" in (card.keywords or [])


def _is_permanent(card) -> bool:
    return not ("Instant" in card.type_line or "Sorcery" in card.type_line)


# ── Consistency ──────────────────────────────────────────────────────────────

TUTOR_LADDER = [(0, 3.5), (12, 4.5), (20, 5.5), (24, 6.25), (32, 7.0),
                (44, 8.0), (56, 9.0), (68, 10.0)]

# The draw column is a table of bands, not a ladder — read the row the total
# falls in. Expressed as (minimum points, score), highest first.
DRAW_BANDS = [(60, 10.0), (40, 9.0), (36, 8.0), (32, 7.0), (24, 6.0),
              (20, 5.0), (12, 3.5), (0, 1.5)]

SELECTION_CAP = 30   # filtering re-sorts draws; the tenth loot finds nothing new
REDUNDANCY_TUTOR_CAP = 7.0


def _tutor_points(card, is_commander: bool = False) -> int:
    """Points for one tutor, per the rubric's tiers."""
    name = _name(card)
    if name in REPEATABLE_TUTOR_ENGINES:
        return 6
    if name in COMBAT_TUTOR_ENGINES:
        return 4
    if name in COMBO_ENABLER_TUTORS:
        return 4
    if name in PREMIUM_TUTORS:
        return 6
    if not brackets.is_tutor(card):
        return 0
    # Unnamed tutors price off cost: the rubric's own split is CMC <= 2
    # unrestricted (premium), 3-4 or restricted (standard), 5+ (narrow).
    if card.cmc <= 2:
        return 6
    if card.cmc <= 4:
        return 4
    return 2


def _draw_points(card, is_commander: bool = False) -> tuple[int, bool]:
    """Points for one draw source, and whether they came from selection.

    Selection points are returned separately because the rubric caps them at 30
    for the deck while the other tiers are uncapped.
    """
    name, text = _name(card), _text(card)
    if name in PUNISHERS:
        return 0, False           # punishers score as Interaction, never here
    if name in BURST_DRAW:
        return 6, False
    if name in PREMIUM_DRAW_ENGINES:
        return 5, False
    # A land that scrys on the way in is a land, not a draw engine.
    if "Land" in card.type_line:
        return 0, False
    if _SELECTION_RE.search(text):
        return 3, True
    if not _draws_for_you(text):
        return 0, False
    if _is_permanent(card) and _REPEATABLE_DRAW_RE.search(text):
        # Symmetric engines hand three opponents cards first — one-shot value.
        if "each player draws" in text or "each opponent draws" in text:
            return 2, False
        # Combat-conditioned draw is not an engine that runs on its own.
        if "attacks" in text or "combat damage" in text:
            return 3, False
        return 4, False
    return 2, False               # one-shot draw


def consistency(cards: list, commander) -> dict:
    """Counted, per the rubric: the tutor ladder and the draw band, joint."""
    tutor_total = selection_total = draw_total = 0
    tutors, draw_sources, premium = [], [], 0

    for card in cards:
        pts = _tutor_points(card)
        if pts:
            tutor_total += pts
            tutors.append(card.name)
            if pts >= 6:
                premium += 1
        dpts, is_selection = _draw_points(card)
        if dpts:
            draw_sources.append(card.name)
            if is_selection:
                selection_total += dpts
            else:
                draw_total += dpts

    if commander is not None:
        if _tutor_points(commander):
            tutor_total += 5          # +5 when the commander is a tutor
            premium += 1
        if _draw_points(commander)[0] >= 4:
            draw_total += 3           # +3 when the commander is a draw engine

    draw_total += min(selection_total, SELECTION_CAP)

    tutor_score = ladder(TUTOR_LADDER, tutor_total)
    draw_score = next(s for floor, s in DRAW_BANDS if draw_total >= floor)

    # Rows 9-10 additionally require two premium-tier tutors; volume alone caps
    # the search column at 8.
    if premium < 2:
        tutor_score = min(tutor_score, 8.0)

    score = min(tutor_score, draw_score)   # joint requirement — the weaker binds
    return {
        "score": snap(score),
        "estimated": False,
        "detail": f"{tutor_total} tutor pts ({len(tutors)} tutors, {premium} premium), "
                  f"{draw_total} draw pts ({len(draw_sources)} sources)",
        "parts": {"tutor_points": tutor_total, "tutor_score": round(tutor_score, 2),
                  "draw_points": draw_total, "draw_score": round(draw_score, 2),
                  "premium_tutors": premium},
    }


# ── Interaction ──────────────────────────────────────────────────────────────

COUNT_LADDER = [(0, 1.0), (3, 3.0), (6, 4.5), (10, 5.5), (14, 6.25),
                (18, 7.0), (22, 8.5), (26, 10.0)]
STACK_LADDER = [(0, 3.5), (6, 4.75), (10, 5.75), (14, 6.5), (20, 7.5),
                (28, 8.25), (38, 9.0), (45, 9.5), (52, 10.0)]

SYMMETRIC_WIPE_CAP = 7.0
CREATURE_ONLY_CAP = 7.0
TWO_CLASS_CAP = 8.0
COUNTERSPELL_SCOPE_WAIVER = 4   # a real counter suite answers anything


def _is_counterspell(card) -> bool:
    return bool(_COUNTER_RE.search(_text(card))) or _name(card) in EFFECTIVE_COUNTERS


def _is_interaction(card) -> bool:
    name, text = _name(card), _text(card)
    if name in PUNISHERS or name in TURN_PROTECTION or name in BOARD_LEVEL_PROTECTION:
        return True
    return bool(
        _is_counterspell(card) or _REMOVAL_RE.search(text) or _WIPE_RE.search(text)
        or _GY_HATE_RE.search(text) or _PROTECTION_RE.search(text)
        or _DETERRENT_RE.search(text) or _STAX_RE.search(text)
        or brackets.is_free_spell(card)
    )


def _scope(card) -> set:
    """Which permanent classes this piece can answer."""
    text = _text(card)
    out = set()
    if re.search(r"target creature|all creatures|creature card|each creature", text):
        out.add("creature")
    if re.search(r"target artifact|all artifacts|artifact card", text):
        out.add("artifact")
    if re.search(r"target enchantment|all enchantments|enchantment card", text):
        out.add("enchantment")
    if re.search(r"target (nonland )?permanent|all (nonland )?permanents|"
                 r"destroy target permanent", text):
        out |= {"creature", "artifact", "enchantment"}
    return out


def interaction(cards: list) -> dict:
    """Counted, per the rubric: piece count, stack/timing points, scope."""
    pieces, stack_points, counters, scope = 0, 0, 0, set()
    symmetric_wipes = 0
    creature_count = sum(1 for c in cards if "Creature" in c.type_line)

    for card in cards:
        if not _is_interaction(card):
            continue
        pieces += 1
        scope |= _scope(card)
        text, name = _text(card), _name(card)

        if _is_counterspell(card):
            stack_points += 2
            counters += 1
        if brackets.is_free_spell(card):
            stack_points += 2
        if name in TURN_PROTECTION:
            stack_points += 2
        if _is_instant_speed(card):
            stack_points += 1
        if _PREMIUM_WIPE_RE.search(text) and card.cmc <= 4:
            stack_points += 1

        # Symmetric wipes cap the score, but a creature wipe in a deck with few
        # creatures is effectively one-sided and does not count toward the cap.
        if _WIPE_RE.search(text) and not re.search(r"exile all|each opponent", text):
            if not ("creature" in text and creature_count <= 8):
                symmetric_wipes += 1

    count_score = ladder(COUNT_LADDER, pieces)
    stack_score = ladder(STACK_LADDER, stack_points)
    score = min(count_score, stack_score)

    if counters < COUNTERSPELL_SCOPE_WAIVER:
        if scope == {"creature"}:
            score = min(score, CREATURE_ONLY_CAP)
        elif len(scope) == 2:
            score = min(score, TWO_CLASS_CAP)
    if symmetric_wipes >= 3:
        score = min(score, SYMMETRIC_WIPE_CAP)

    return {
        "score": snap(score),
        "estimated": False,
        "detail": f"{pieces} pieces, {stack_points} stack pts, "
                  f"{counters} counters, covers {'/'.join(sorted(scope)) or 'nothing'}",
        "parts": {"pieces": pieces, "stack_points": stack_points,
                  "counters": counters, "scope": sorted(scope),
                  "symmetric_wipes": symmetric_wipes},
    }


# ── Speed (estimated) ────────────────────────────────────────────────────────

# Fundamental turn -> score, from the rubric's table. Read as "wins on turn N".
TURN_TO_SPEED = {1: 10.0, 2: 10.0, 3: 9.0, 4: 8.0, 5: 7.0, 6: 6.0,
                 7: 5.0, 8: 4.0, 9: 4.0, 10: 3.0, 11: 3.0, 12: 2.0, 13: 2.0}
SLOWEST_TURN = 14
BATTLECRUISER_TURN = 9    # where a deck with no acceleration lands


def speed(cards: list, combos, consistency_score: float) -> dict:
    """Estimated. The rubric wants a goldfish; this reads the deck's gears.

    Where Commander Spellbook found a two-card combo it reports the earliest
    turn that line assembles, which is real data and is used directly. Failing
    that, the estimate starts at a battlecruiser turn 9 and pulls it in for
    acceleration, a cheap curve, and effects that put threats into play early.
    """
    why = []
    combo_turn = None
    if combos is not None and getattr(combos, "checked", False) and combos.two_card:
        speeds = [c["speed"] for c in combos.two_card if c.get("speed")]
        if speeds:
            combo_turn = min(speeds)
            why.append(f"Spellbook puts the fastest two-card line on turn {combo_turn}")

    nonland = [c for c in cards if "Land" not in c.type_line]
    avg_mv = sum(c.cmc for c in nonland) / len(nonland) if nonland else 3.0
    fast_mana = sum(1 for c in cards if brackets.is_fast_mana(c))
    # Ramp means accelerating *past* one land a turn. Lands are excluded or
    # every deck reads as 36 ramp sources — they are the baseline, not the
    # acceleration.
    ramp = sum(1 for c in nonland
               if re.search(r"add \{[wubrgc0-9]\}|search your library for a .{0,20}land",
                            _text(c)))
    cheats = sum(1 for c in cards if _CHEAT_RE.search(_text(c)))

    turn = float(BATTLECRUISER_TURN)
    if avg_mv <= 2.0:
        turn -= 2.0
        why.append(f"curve at {avg_mv:.1f} average mana value")
    elif avg_mv <= 2.6:
        turn -= 1.0
        why.append(f"curve at {avg_mv:.1f} average mana value")
    elif avg_mv >= 3.6:
        turn += 1.0
        why.append(f"heavy curve at {avg_mv:.1f} average mana value")
    if fast_mana >= 4:
        turn -= 1.5
        why.append(f"{fast_mana} pieces of fast mana")
    elif fast_mana >= 2:
        turn -= 1.0
        why.append(f"{fast_mana} pieces of fast mana")
    if ramp >= 12:
        turn -= 1.0
        why.append(f"{ramp} ramp sources")
    if cheats >= 4:
        turn -= 1.0
        why.append(f"{cheats} ways to cheat threats into play")

    if combo_turn is not None:
        # A combo the deck can assemble is the fundamental turn when it beats
        # the fair plan; the rubric tie-breaks conservatively, so take the later.
        turn = min(turn, float(combo_turn))
    estimated_turn = max(1, min(SLOWEST_TURN, round(turn)))
    score = TURN_TO_SPEED.get(estimated_turn, 1.0)

    # Speed/Consistency coupling: a 9-10 needs the search to back it up.
    capped = False
    if score >= 9.0 and consistency_score <= 7.0:
        score, capped = 8.0, True
        why.append("capped at 8 — Consistency 7 or lower can't support a turn-3 win")

    return {
        "score": snap(score),
        "estimated": True,
        "detail": f"estimated fundamental turn {estimated_turn}"
                  + (f" — {'; '.join(why)}" if why else ""),
        "parts": {"turn": estimated_turn, "avg_mv": round(avg_mv, 2),
                  "fast_mana": fast_mana, "ramp": ramp, "cheats": cheats,
                  "combo_turn": combo_turn, "consistency_capped": capped},
    }


# ── Resilience (estimated) ───────────────────────────────────────────────────

RECURSION_ENGINE_RE = re.compile(
    r"at the beginning of your (upkeep|end step).{0,60}return|"
    r"return .{0,30}from your graveyard to the battlefield"
)
COMMANDER_DEPENDENCY_DEFAULT = -1.0   # "Moderate" is the format default


def _distinct_lines(two_card: list) -> float:
    """Count win layers, not combo rows.

    Spellbook lists every pair, so one hub card can produce six rows — a Vito
    deck with Exquisite Blood shows Sanguine Bond + Blood, Cliffhaven Vampire +
    Blood, Epicure + Blood and so on. Those are one line with interchangeable
    halves, and the rubric is explicit that lines sharing a single point of
    failure count 1.5 rather than 2: answering Exquisite Blood answers all of
    them. Group by shared card, then price each group as a line plus a half for
    the redundancy inside it.
    """
    groups: list[set] = []
    for combo in two_card:
        names = {n.lower() for n in combo.get("cards", []) if n}
        for group in groups:
            if group & names:
                group |= names
                break
        else:
            groups.append(set(names))

    total = 0.0
    for group in groups:
        rows = sum(1 for c in two_card
                   if {n.lower() for n in c.get("cards", [])} & group)
        total += 1.0 if rows == 1 else 1.5
    return total


def resilience(cards: list, combos, tutor_points: int) -> dict:
    """Estimated. Combo layering and recursion are counted; the commander
    dependency penalty is assumed to be the format default, since judging it
    means knowing how the deck plays without its commander.
    """
    lines = 0.0
    if combos is not None and getattr(combos, "checked", False):
        lines = _distinct_lines(combos.two_card)
        # Assembly-adjusted: a line the deck cannot reliably find is not a full
        # win layer, and credit scales with search access.
        if tutor_points < 12:
            lines *= 0.5
        elif tutor_points < 24:
            lines *= 0.75

    recursion_pts = sum(2 if RECURSION_ENGINE_RE.search(_text(c)) else 1
                        for c in cards if _RECURSION_RE.search(_text(c)))
    protection = sum(1 for c in cards
                     if _PROTECTION_RE.search(_text(c)) or _DETERRENT_RE.search(_text(c)))
    board_level = sum(1 for c in cards if _name(c) in BOARD_LEVEL_PROTECTION)
    threats = sum(1 for c in cards
                  if "Creature" in c.type_line
                  and (_power(c) >= 4 or "Flying" in (c.keywords or [])
                       or "Deathtouch" in (c.keywords or []))
                  or "Planeswalker" in c.type_line)

    # Combo channel, from the rubric's rows.
    if lines >= 3:
        score = 10.0
    elif lines >= 2:
        score = 9.0
    elif lines >= 1.5:
        score = 8.0
    elif lines >= 1:
        score = 6.0        # a lone line with no backup caps at 6
    elif lines >= 0.75:
        score = 5.0
    elif lines >= 0.5:
        score = 3.5
    else:
        score = 0.0

    # Combat channel — structure, not just totals.
    combat = 0.0
    if threats >= 12 and protection >= 8 and recursion_pts >= 12 and board_level >= 3:
        combat = 9.0
    elif threats >= 12 and protection >= 6 and recursion_pts >= 10 and board_level >= 2:
        combat = 8.0
    elif threats >= 10 and protection >= 4 and recursion_pts >= 8 and board_level >= 1:
        combat = 6.0
    elif threats >= 8 and protection >= 2 and recursion_pts >= 4:
        combat = 5.0
    elif threats >= 6 and protection >= 1 and recursion_pts >= 2:
        combat = 4.5
    elif threats >= 3:
        combat = 3.5
    else:
        combat = 2.5

    score = max(score, combat) + COMMANDER_DEPENDENCY_DEFAULT
    return {
        "score": snap(score),
        "estimated": True,
        "detail": f"{lines:.2g} combo line(s), {recursion_pts} recursion pts, "
                  f"{threats} threats, {protection} protection "
                  f"({board_level} board-level); commander dependency assumed moderate",
        "parts": {"combo_lines": lines, "recursion_points": recursion_pts,
                  "threats": threats, "protection": protection,
                  "board_level_protection": board_level},
    }


def _power(card) -> int:
    try:
        return int(card.power)
    except (TypeError, ValueError):
        return 0


# ── The score ────────────────────────────────────────────────────────────────

def evaluate(commander, deck: list, combos=None) -> dict:
    """Score `deck` on all four attributes and average them into CRISPI."""
    cards = [c for c in deck if not c.is_basic_filler]
    if commander is not None:
        cards = [commander] + cards

    con = consistency(cards, commander)
    inter = interaction(cards)
    spd = speed(cards, combos, con["score"])
    res = resilience(cards, combos, con["parts"]["tutor_points"])

    attributes = {"consistency": con, "interaction": inter,
                  "speed": spd, "resilience": res}
    score = snap(sum(a["score"] for a in attributes.values()) / 4)
    result = {
        "score": score,
        "attributes": attributes,
        "estimated": [k for k, a in attributes.items() if a["estimated"]],
        "summary": f"CRISPI {score:.2f} — "
                   f"C {con['score']:.2f} · R {res['score']:.2f} · "
                   f"I {inter['score']:.2f} · S {spd['score']:.2f}",
    }
    # Carried on the result so `brackets.py` can apply the floor without
    # importing this module — it already imports `brackets` for the detectors,
    # and the cycle would be fragile.
    number, why = bracket_floor(result)
    result["floor"] = {"bracket": number, "reason": why} if number else None
    return result


# ── Bracket floors ───────────────────────────────────────────────────────────
# DeckCheck's guardrails: the published card restrictions decide a bracket, and
# these catch decks that obey them while playing well above them. They only ever
# bump a deck up — nobody objects to a weak deck sitting in a high bracket.

def bracket_floor(crispi: dict) -> tuple[int, str] | tuple[None, None]:
    """The highest bracket floor this deck trips, and why.

    Returns (bracket, reason) or (None, None). Half-step Speed ratings get the
    benefit of the doubt: 8.5 means "turn 3 or turn 4 depending on the draw", so
    it counts as the slower turn and does not trip the next floor up.
    """
    score = crispi["score"]
    spd = crispi["attributes"]["speed"]["score"]
    con = crispi["attributes"]["consistency"]["score"]
    inter = crispi["attributes"]["interaction"]["score"]

    if spd >= 9.0 or score >= 8.5:
        return 5, ("Speed 9+" if spd >= 9.0 else f"CRISPI {score:.2f}")
    if spd >= 8.0 or score >= 7.0 or (con >= 7.5 and inter >= 7.5):
        if spd >= 8.0:
            why = "Speed 8+"
        elif score >= 7.0:
            why = f"CRISPI {score:.2f}"
        else:
            why = f"Consistency {con:.2f} and Interaction {inter:.2f} both 7.5+"
        return 4, why
    if spd >= 6.0 or score >= 5.0:
        return 3, ("Speed 6+" if spd >= 6.0 else f"CRISPI {score:.2f}")
    if spd >= 5.0 or score >= 3.5:
        return 2, ("Speed 5+" if spd >= 5.0 else f"CRISPI {score:.2f}")
    return None, None
