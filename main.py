import sys
from pathlib import Path

import click

import brackets
import crispi
import formats
import precons
from arena_collection import load_owned_cards
from combos import find_combos
from card_data import load_scryfall_lookup, load_by_name, enrich_collection
from commander import find_commanders
from deck_builder import build_deck, deck_archetype as commander_archetype
from edhrec_recs import recommend
from standard_builder import build_standard_deck, deck_archetype as constructed_archetype
from output import print_and_save, print_and_save_standard


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
@click.option(
    "--precons", "precons_mode", is_flag=True,
    help="Instead of building a deck, find the preconstructed Commander decks in "
         "the collection and suggest swaps for each from the cards you own. "
         "Needs EDHREC the first time; cached for a week after that.",
)
@click.option(
    "--include-deck-cards", is_flag=True,
    help="With --precons: also consider cards that sit in another of your decks "
         "(a ManaBox binder matching another precon). Off by default, because "
         "moving them breaks that deck.",
)
@click.option(
    "--precon-commanders", default=None, metavar="NAMES",
    help="With --precons: who helms the deck, e.g. \"Leonardo, the Balance // "
         "Michelangelo, the Heart\". Applies to every detected precon whose list "
         "contains those cards; the rest use EDHREC's default.",
)
def main(collection_path: str, output: str, no_recs: bool, fmt: str, colors: str | None,
         pick: int, target_bracket: int | None, precons_mode: bool,
         include_deck_cards: bool, precon_commanders: str | None):
    """Build a Commander, Brawl, Standard or Pauper deck from your collection.

    Accepts a ManaBox CSV export, an MTG Arena deck export
    (lines of the form: N Card Name (SET) collector#), or an Arena
    collection CSV scraped from Player.log.

    With --precons it does something else with the same file: recognises the
    precons you own and says what to swap into each.
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

    if precons_mode:
        names = [n.strip() for n in (precon_commanders or "").split("//") if n.strip()]
        _precon_report(owned, output, include_deck_cards, names)
        return

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
        found = find_combos(commander.name, deck, commander=commander)
    score = crispi.evaluate(commander, deck, found)
    bracket = brackets.evaluate(commander, deck, found, fmt=fmt,
                                target=target_bracket, crispi=score)
    if bracket["target"]:
        print()
        print(bracket["target"]["line"])

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


def _precon_report(owned: list, output: str, include_deck_cards: bool,
                   commander_names: list):
    """The --precons path: detect, suggest, print, and save one import list per precon."""
    print("\nLooking for preconstructed decks...")
    try:
        found = precons.detect(owned)
    except Exception as exc:  # EDHREC unreachable on a cold cache
        click.echo(f"Could not reach EDHREC's precon index: {exc}", err=True)
        sys.exit(1)
    if not found:
        print("No preconstructed decks found in this collection.")
        return
    print("Found: " + ", ".join(f"{d.precon.name} ({d.coverage:.0%})" for d in found))

    card_index = load_by_name()
    reserved = {d.binder for d in found if d.binder}
    results = []
    for det in found:
        wanted = commander_names if all(n.lower() in {x.lower() for x, _ in det.precon.cards}
                                        for n in commander_names) else None
        results.append((det, precons.suggest_swaps(
            det, owned, card_index, reserved_binders=reserved,
            include_deck_cards=include_deck_cards, commander_names=wanted or None)))
    report = precons.format_report(results)
    print()
    print(report)

    # The generic default is a deck file name; a precon report deserves its own.
    out = Path("precon_swaps.txt") if output == "deck_output.txt" else Path(output)
    out.write_text(report, encoding="utf-8")
    print(f"Saved to: {out}")
    for det, result in results:
        if result.get("error") or not result["swaps"]:
            continue
        path = out.with_name(f"{out.stem}.{det.precon.info.slug}.decklist.txt")
        path.write_text(precons.decklist_text(result["commanders"], result["after"]),
                        encoding="utf-8")
        print(f"Import-ready list after swaps: {path}")


if __name__ == "__main__":
    main()
