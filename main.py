import sys

import click
from dotenv import load_dotenv

load_dotenv()

from arena_collection import load_owned_cards
from card_data import load_scryfall_lookup, enrich_collection
from commander import find_commanders
from deck_builder import build_deck
from standard_builder import build_standard_deck
from ai_advisor import get_deck_review, get_standard_review
from output import print_and_save, print_and_save_standard


@click.command()
@click.argument("collection_path", default="ManaBox_Collection.csv")
@click.option("--output", "-o", default="deck_output.txt", show_default=True, help="Output file path.")
@click.option("--no-ai", is_flag=True, help="Skip Claude AI review (no ANTHROPIC_API_KEY needed).")
@click.option(
    "--format", "fmt",
    type=click.Choice(["commander", "standard"]),
    default="commander",
    show_default=True,
    help="Deck format to build.",
)
@click.option(
    "--colors",
    default=None,
    metavar="WUBRG",
    help="Force deck colors for standard (e.g. W or UG). Default: auto-pick strongest.",
)
@click.option(
    "--pick",
    default=1,
    show_default=True,
    metavar="N",
    help="Use the Nth-best scoring commander instead of the top one (commander format only).",
)
def main(collection_path: str, output: str, no_ai: bool, fmt: str, colors: str | None, pick: int):
    """Build a Commander or Standard deck from your collection.

    Accepts a ManaBox CSV export, an MTG Arena deck export
    (lines of the form: N Card Name (SET) collector#), or an Arena
    collection CSV scraped from Player.log.
    """
    # ── 1. Parse collection ────────────────────────────────────────────────
    print(f"Parsing collection: {collection_path}")
    lookup, by_set_cn = load_scryfall_lookup()
    owned = load_owned_cards(collection_path, by_set_cn)
    print(f"Found {len(owned)} unique card(s) in your collection.")

    # ── 2. Enrich with Scryfall metadata ──────────────────────────────────
    enrich_collection(owned, lookup)

    # ── Standard path ──────────────────────────────────────────────────────
    if fmt == "standard":
        color_set = set(colors.upper()) if colors else None
        print("\nBuilding Standard deck...")
        deck_entries, used_colors = build_standard_deck(owned, colors=color_set)
        total = sum(e.count for e in deck_entries)
        assert total == 60, f"Expected 60 cards, got {total}"

        review = ""
        if not no_ai:
            print("Requesting AI deck review from Claude...")
            review = get_standard_review(deck_entries, used_colors)
        print()
        print_and_save_standard(deck_entries, used_colors, review, output)
        return

    # ── 3. Find commander candidates ──────────────────────────────────────
    print("\nScoring commander candidates...")
    commanders = find_commanders(owned)
    if not commanders:
        click.echo("No Commander-legal legendary creatures found in your collection.", err=True)
        sys.exit(1)

    print(f"\nTop commander candidates:")
    for i, (cmd, score) in enumerate(commanders[:7]):
        ci = "".join(cmd.color_identity) or "C"
        print(f"  {i + 1:2}. {cmd.name:<40} [{ci}]  {int(score)} compatible cards")

    idx = min(pick - 1, len(commanders) - 1)
    commander, _ = commanders[idx]
    print(f"\nSelected commander: {commander.name}")

    # ── 4. Build the 99-card deck ──────────────────────────────────────────
    print("Building deck...")
    deck = build_deck(commander, owned)

    # Quick sanity checks
    assert len(deck) == 99, f"Expected 99 cards, got {len(deck)}"
    ci_set = set(commander.color_identity)
    violations = [c for c in deck if not set(c.color_identity).issubset(ci_set)]
    if violations:
        click.echo(f"Warning: {len(violations)} card(s) violate color identity — please report this bug.", err=True)

    # ── 5. Claude review ───────────────────────────────────────────────────
    review = ""
    if not no_ai:
        print("Requesting AI deck review from Claude...")
        review = get_deck_review(commander, deck)

    # ── 6. Output ──────────────────────────────────────────────────────────
    print()
    print_and_save(commander, deck, review, output)


if __name__ == "__main__":
    main()
