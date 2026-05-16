import sys

import click
from dotenv import load_dotenv

load_dotenv()

from collection import parse_collection
from card_data import load_scryfall_lookup, enrich_collection
from commander import find_commanders
from deck_builder import build_deck
from ai_advisor import get_deck_review
from output import print_and_save


@click.command()
@click.argument("csv_path", default="ManaBox_Collection.csv")
@click.option("--output", "-o", default="deck_output.txt", show_default=True, help="Output file path.")
@click.option("--no-ai", is_flag=True, help="Skip Claude AI review (no ANTHROPIC_API_KEY needed).")
@click.option(
    "--pick",
    default=1,
    show_default=True,
    metavar="N",
    help="Use the Nth-best scoring commander instead of the top one.",
)
def main(csv_path: str, output: str, no_ai: bool, pick: int):
    """Build a Commander deck from your ManaBox collection CSV.

    Reads your collection, selects the best commander by heuristic scoring,
    assembles 99 cards by functional role + synergy, and optionally asks
    Claude for a strategic review.
    """
    # ── 1. Parse collection ────────────────────────────────────────────────
    print(f"Parsing collection: {csv_path}")
    owned = parse_collection(csv_path)
    print(f"Found {len(owned)} unique card(s) in your collection.")

    # ── 2. Enrich with Scryfall metadata ──────────────────────────────────
    lookup = load_scryfall_lookup()
    enrich_collection(owned, lookup)

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
