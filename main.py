import sys

import click

import brackets
import formats
from arena_collection import load_owned_cards
from combos import find_combos
from card_data import load_scryfall_lookup, load_by_name, enrich_collection
from commander import find_commanders
from deck_builder import build_deck, deck_archetype as commander_archetype
from edhrec_recs import recommend
from standard_builder import build_standard_deck, deck_archetype as constructed_archetype
from output import print_and_save, print_and_save_standard


def _target_report(target: int, bracket: dict) -> str:
    """Whether `--target-bracket` got what it asked for, and what to do if not.

    The builder controls the card-level criteria, so it hits the target or is
    stopped by something it cannot put in the deck — a Game Changer the
    collection does not hold, or a combo it could not see coming.
    """
    got = bracket["number"]
    name = brackets.BRACKET_NAMES[target]
    crit = {c["key"]: c for c in bracket["criteria"]}
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
            return ("Target bracket 1: built inside Exhibition's restrictions — no Game "
                    "Changers, no land denial, no extra turns, no combos. It is reported "
                    "as 2 because Exhibition is a claim about why a deck was built, which "
                    "a decklist cannot make for you." + caveat)
        return (f"Target bracket 1: missed — the deck came out as {bracket['label']}, "
                f"{bracket['reason']}.")

    if got == target:
        return f"Target bracket {target} ({name}): met — {bracket['reason']}.{caveat}"

    if got > target:
        return (f"Target bracket {target} ({name}): missed — the deck came out as "
                f"{bracket['label']}, {bracket['reason']}. The builder keeps out what a "
                f"bracket disallows card by card, but two-card combos only show up once "
                f"the pairs are together, so they can still push a deck past its target.")

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
    return (f"Target bracket {target} ({name}): not reached — the deck is "
            f"{bracket['label']}, because {why}. Play it a bracket up if the "
            f"table wants to.")


@click.command()
@click.argument("collection_path", default="ManaBox_Collection.csv")
@click.option("--output", "-o", default="deck_output.txt", show_default=True, help="Output file path.")
@click.option("--no-recs", "--no-ai", "no_recs", is_flag=True,
              help="Skip the EDHREC recommendation pass (works offline).")
@click.option(
    "--format", "fmt",
    type=click.Choice(list(formats.FORMATS)),
    default="commander",
    show_default=True,
    help="Deck format to build.",
)
@click.option(
    "--colors",
    default=None,
    metavar="WUBRG",
    help="Force deck colors for constructed formats (e.g. W or UG). Default: auto-pick strongest.",
)
@click.option(
    "--pick",
    default=1,
    show_default=True,
    metavar="N",
    help="Use the Nth-best scoring commander instead of the top one (commander format only).",
)
@click.option(
    "--target-bracket",
    type=click.IntRange(1, 5),
    default=None,
    metavar="N",
    help="Build to a Commander bracket (1-5): keep out what the bracket "
         "disallows, and for bracket 3 put in the Game Changers that reach it. "
         "Two-card combos are reported against the target, not built around.",
)
def main(collection_path: str, output: str, no_recs: bool, fmt: str, colors: str | None,
         pick: int, target_bracket: int | None):
    """Build a Commander, Brawl, Standard or Pauper deck from your collection.

    Accepts a ManaBox CSV export, an MTG Arena deck export
    (lines of the form: N Card Name (SET) collector#), or an Arena
    collection CSV scraped from Player.log.
    """
    spec = formats.get(fmt)
    if target_bracket is not None and not spec.singleton:
        click.echo(f"--target-bracket is a Commander framework and does not apply "
                   f"to {spec.label}.", err=True)
        sys.exit(1)

    # ── 1. Parse collection ────────────────────────────────────────────────
    print(f"Parsing collection: {collection_path}")
    lookup, by_set_cn = load_scryfall_lookup()
    owned = load_owned_cards(collection_path, by_set_cn)
    print(f"Found {len(owned)} unique card(s) in your collection.")

    # ── 2. Enrich with Scryfall metadata ──────────────────────────────────
    enrich_collection(owned, lookup)

    # ── Constructed path (Standard, Pauper) ────────────────────────────────
    if not spec.singleton:
        color_set = set(colors.upper()) if colors else None
        print(f"\nBuilding {spec.label} deck...")
        deck_entries, used_colors, sideboard = build_standard_deck(
            owned, fmt=fmt, colors=color_set
        )
        total = sum(e.count for e in deck_entries)
        assert total == spec.deck_size, f"Expected {spec.deck_size} cards, got {total}"

        name = constructed_archetype(deck_entries, used_colors)
        print()
        print_and_save_standard(deck_entries, used_colors, output, sideboard,
                                format_label=spec.label, name=name)
        return

    # ── 3. Find commander candidates ──────────────────────────────────────
    print("\nScoring commander candidates...")
    commanders = find_commanders(owned, fmt=fmt)
    if not commanders:
        click.echo(f"No {spec.label}-legal legendary creatures found in your collection.", err=True)
        sys.exit(1)

    print(f"\nTop commander candidates:")
    for i, (cmd, score) in enumerate(commanders[:7]):
        ci = "".join(cmd.color_identity) or "C"
        print(f"  {i + 1:2}. {cmd.name:<40} [{ci}]  {int(score)} compatible cards")

    idx = min(pick - 1, len(commanders) - 1)
    commander, _ = commanders[idx]
    print(f"\nSelected commander: {commander.name}")

    # ── 4. Build the deck ──────────────────────────────────────────────────
    if target_bracket is None:
        print("Building deck...")
    else:
        print(f"Building deck for bracket {target_bracket} "
              f"({brackets.BRACKET_NAMES[target_bracket]})...")
    deck = build_deck(commander, owned, fmt=fmt, target_bracket=target_bracket)

    # Quick sanity checks
    expected = spec.deck_size - 1
    assert len(deck) == expected, f"Expected {expected} cards, got {len(deck)}"
    ci_set = set(commander.color_identity)
    violations = [c for c in deck if not set(c.color_identity).issubset(ci_set)]
    if violations:
        click.echo(f"Warning: {len(violations)} card(s) violate color identity — please report this bug.", err=True)

    # ── 5. Bracket ────────────────────────────────────────────────────────
    # --no-recs is the offline switch, and the combo lookup is the only part of
    # the bracket that needs the network; the rest is computed either way.
    found = None
    if not no_recs:
        print("Checking for two-card combos...")
        found = find_combos(commander.name, deck)
    bracket = brackets.evaluate(commander, deck, found, fmt=fmt)
    if target_bracket is not None:
        print()
        print(_target_report(target_bracket, bracket))

    # ── 6. EDHREC recommendations ─────────────────────────────────────────
    recs = None
    if not no_recs:
        print("Fetching EDHREC recommendations...")
        recs = recommend(commander, owned, deck, card_index=load_by_name())

    # ── 7. Output ──────────────────────────────────────────────────────────
    print()
    print_and_save(commander, deck, output, recs,
                   format_label=spec.label, name=commander_archetype(deck, commander),
                   bracket=bracket)


if __name__ == "__main__":
    main()
