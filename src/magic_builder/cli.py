import sys
from pathlib import Path

import click

from magic_builder import paths
from magic_builder.analysis import brackets, crispi, meta, precons
from magic_builder.analysis.combos import find_combos
from magic_builder.builders import formats
from magic_builder.builders.candidates import find_commanders
from magic_builder.builders.commander import (
    build_deck,
    deck_archetype as commander_archetype,
)
from magic_builder.builders.standard import (
    build_standard_deck,
    deck_archetype as constructed_archetype,
)
from magic_builder.collection.arena import detect_collection_format, load_owned_cards
from magic_builder.data import mtggoldfish
from magic_builder.data.edhrec import recommend
from magic_builder.data.scryfall import (
    enrich_collection,
    load_by_name,
    load_scryfall_lookup,
)
from magic_builder.output import print_and_save, print_and_save_standard


def _version() -> str:
    """The installed version, or `unknown` — never a guess.

    Read from the package metadata rather than a literal, so it cannot disagree with what pip installed.
    A number typed into the source is a number that goes stale the first time someone forgets it.
    """
    try:
        from importlib.metadata import version
        return version("magic-builder")
    except Exception:
        return "unknown"


def _print_version(ctx, param, value):
    """`--version`: the version, and the Python it is running on.

    The interpreter matters here more than usual — the Scryfall bulk file and CatBoost-style wheels are
    where a version floor bites — so printing both turns "it does not work" into a checkable fact.
    """
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"magic-builder {_version()} (python {sys.version.split()[0]})")
    ctx.exit()



@click.command()
@click.argument("collection_path", default="ManaBox_Collection.csv")
@click.option("--demo", is_flag=True,
              help="Build from a small bundled example collection — no file needed, nothing to "
                   "export. The quickest way to see what this does.")
@click.option("--version", is_flag=True, is_eager=True, expose_value=False,
              callback=_print_version,
              help="Show the version and exit.")
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
@click.option(
    "--meta", "meta_fmt", default=None,
    type=click.Choice(list(mtggoldfish.FORMATS)),
    help="Instead of building a deck, rank the format's current MTGGoldfish "
         "archetypes by how much of each list you own, with what is missing and "
         "what it costs. Saves the closest list to --output.",
)
def main(collection_path: str, demo: bool, output: str, no_recs: bool, fmt: str, colors: str | None,
         pick: int, target_bracket: int | None, precons_mode: bool,
         include_deck_cards: bool, precon_commanders: str | None, meta_fmt: str | None):
    """Build a Commander, Brawl, Standard, Pauper or Modern deck from your collection.

    \b
    New here? Try it without exporting anything:

        magic-builder --demo

    That builds from a small example collection bundled with the program. When you want your own
    cards, export your collection from ManaBox (or MTG Arena) and pass the file:

    \b
        magic-builder ~/Downloads/ManaBox_Collection.csv -o my_deck.txt

    Accepts a ManaBox CSV export, an MTG Arena deck export
    (lines of the form: N Card Name (SET) collector#), or an Arena
    collection CSV scraped from Player.log.

    The first run downloads Scryfall's card database (~80 MB, cached for a week) —
    that is the only time it does.

    With --precons it does something else with the same file: recognises the
    precons you own and says what to swap into each. With --meta FORMAT it
    ranks the current meta decks by how close the collection is to each.
    """
    spec = formats.get(fmt)
    if target_bracket is not None and not spec.singleton:
        click.echo(f"--target-bracket is a Commander framework and does not apply "
                   f"to {spec.label}.", err=True)
        sys.exit(1)

    # ── 0. Where the cards come from ───────────────────────────────────────
    # The order matters: an explicit path wins, then --demo, then the default name. A path the user
    # typed is never overridden by a flag they also typed by accident without us saying so.
    if demo and collection_path != "ManaBox_Collection.csv":
        raise click.UsageError("--demo builds from the bundled example; do not also pass a file.")
    if demo:
        collection_path = str(paths.example_collection())
        if not Path(collection_path).exists():
            # Only reachable from a broken install, and worth its own message: the fix is to reinstall,
            # not to find a file.
            raise click.ClickException(
                "the bundled example collection is missing from this installation — "
                "reinstall magic-builder, or pass your own collection file."
            )
    elif not Path(collection_path).exists():
        # The first thing anyone does is run the command with no arguments, and the default name does
        # not exist on a machine that has never exported anything. A traceback there is the worst
        # possible introduction, so say what to do instead.
        raise click.ClickException(
            f"no collection file at {collection_path!r}.\n"
            f"  To try it first with no setup:      magic-builder --demo\n"
            f"  To use your own cards:              magic-builder <your-export.csv>\n"
            f"  Supported exports: a ManaBox CSV, an MTG Arena deck export, or an Arena\n"
            f"  collection CSV from Player.log. See the README for where to get each."
        )

    # ── 1. Parse collection ────────────────────────────────────────────────
    print(f"Parsing collection: {collection_path}")
    # Detect before loading, so a file in no known format is refused rather than read as an Arena export
    # and reported as "Found 0 unique card(s)" — which reads as a fact about the collection instead of a
    # fact about the file. Everything downstream would then quietly work off an empty binder.
    if detect_collection_format(collection_path) == "unknown":
        # ClickException rather than sys.exit: it prints the message and nothing else. A traceback in
        # front of "this file is not a collection" tells the user they broke something when they did not.
        raise click.ClickException(
            f"{collection_path} is not a collection file I recognise.\n"
            f"  Supported: a ManaBox CSV export (has a 'Scryfall ID' column), an MTG Arena deck\n"
            f"  export ('1 Sol Ring (TRC) 499' per line), or an Arena collection CSV scraped from\n"
            f"  Player.log (has 'grp_id' and 'collector_number' columns).\n"
            f"  Nothing was read."
        )
    lookup, by_set_cn = load_scryfall_lookup()
    owned = load_owned_cards(collection_path, by_set_cn)
    print(f"Found {len(owned)} unique card(s) in your collection.")

    # ── 2. Enrich with Scryfall metadata ──────────────────────────────────
    enrich_collection(owned, lookup)

    if meta_fmt:
        _meta_report(owned, meta_fmt, output)
        return

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

    print("\nTop commander candidates:")
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


def _meta_report(owned: list, fmt: str, output: str):
    """The --meta path: rank the meta, print it, save the closest list."""
    print(f"\nReading the {formats.get(fmt).label} metagame from MTGGoldfish...")
    try:
        found = meta.match(owned, fmt)
    except mtggoldfish.FETCH_ERRORS as exc:
        click.echo(f"Could not read MTGGoldfish's {fmt} metagame: {exc}", err=True)
        sys.exit(1)
    if not found:
        click.echo("No archetype lists could be read.", err=True)
        sys.exit(1)
    print()
    print(meta.format_report(found, fmt))
    best = found[0]
    path = Path(output).with_suffix(".decklist.txt")
    path.write_text(meta.decklist_text(best), encoding="utf-8")
    print(f"Closest list ({best.archetype.name}) saved to {path}")


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
