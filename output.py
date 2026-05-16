from pathlib import Path

from collection import OwnedCard


def _categorize(cards: list) -> dict:
    order = ["Creatures", "Planeswalkers", "Instants", "Sorceries", "Enchantments", "Artifacts", "Lands", "Other"]
    cats: dict[str, list] = {k: [] for k in order}
    for c in cards:
        tl = c.type_line
        if "Creature" in tl:
            cats["Creatures"].append(c)
        elif "Planeswalker" in tl:
            cats["Planeswalkers"].append(c)
        elif "Instant" in tl:
            cats["Instants"].append(c)
        elif "Sorcery" in tl:
            cats["Sorceries"].append(c)
        elif "Enchantment" in tl:
            cats["Enchantments"].append(c)
        elif "Artifact" in tl:
            cats["Artifacts"].append(c)
        elif "Land" in tl:
            cats["Lands"].append(c)
        else:
            cats["Other"].append(c)
    return cats


def _sort_key(card: OwnedCard):
    return (card.cmc, card.name)


def format_deck(commander: OwnedCard, deck: list, review: str = "") -> str:
    SEP = "═" * 60
    DIV = "─" * 40
    lines = [SEP, "  COMMANDER DECK RECOMMENDATION", SEP, ""]

    lines += ["Commander (1)", DIV, f"1 {commander.name}", ""]

    cats = _categorize(deck)
    for cat_name, cards in cats.items():
        if not cards:
            continue
        fillers = [c for c in cards if c.is_basic_filler]
        owned = [c for c in cards if not c.is_basic_filler]

        lines.append(f"{cat_name} ({len(cards)})")
        lines.append(DIV)
        for c in sorted(owned, key=_sort_key):
            lines.append(f"1 {c.name}")
        for c in sorted(fillers, key=lambda x: x.name):
            lines.append(f"1 {c.name}  * basic filler")
        lines.append("")

    total = len(deck) + 1
    fillers_count = sum(1 for c in deck if c.is_basic_filler)
    lines.append(f"Total: {total} cards  (1 commander + {len(deck)} main deck)")
    if fillers_count:
        lines.append(f"Note: {fillers_count} basic land(s) added as filler — not from your collection.")

    if review:
        lines += ["", SEP, "  AI DECK ANALYSIS (Claude)", SEP, "", review]

    return "\n".join(lines)


def format_decklist(commander: OwnedCard, deck: list) -> str:
    """Plain importable deck list format (Moxfield/Archidekt compatible)."""
    lines = [f"1 {commander.name} *CMDR*", ""]
    for c in sorted(deck, key=_sort_key):
        lines.append(f"1 {c.name}")
    return "\n".join(lines)


def print_and_save(commander: OwnedCard, deck: list, review: str, output_path: str):
    pretty = format_deck(commander, deck, review)
    importable = format_decklist(commander, deck)

    print(pretty)

    Path(output_path).write_text(pretty, encoding="utf-8")

    import_path = Path(output_path).with_suffix(".decklist.txt")
    import_path.write_text(importable, encoding="utf-8")

    print(f"\nSaved to: {output_path}")
    print(f"Import-ready list: {import_path}")
