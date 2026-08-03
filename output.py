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


def deck_price(cards: list, counts: dict | None = None) -> float:
    """Total USD of a card list; `counts` maps name -> copies for constructed decks."""
    return sum(
        (card.price_usd or 0.0) * (counts.get(card.name, 1) if counts else 1)
        for card in cards
    )


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def format_recommendations(recs: dict) -> str:
    """Render the EDHREC upgrade / acquisition split as a text section."""
    SEP = "═" * 60
    DIV = "─" * 40
    lines = [SEP, "  EDHREC RECOMMENDATIONS", SEP, ""]

    if recs.get("error"):
        return "\n".join(lines + [recs["error"]])

    lines.append(
        f"{recs['in_deck']} of EDHREC's {recs['total']} recommended cards are already in this deck."
    )
    lines.append("")

    def row(r) -> str:
        price = _money(r.price_usd) if r.price_usd else "—"
        return (f"{r.name:<34} {r.role:<7} {r.inclusion:>4.0%} of decks   "
                f"synergy {r.synergy:+.2f}   {price:>9}")

    upgrades = recs.get("upgrades") or []
    lines.append(f"Own it, not in the deck ({len(upgrades)})")
    lines.append(DIV)
    if upgrades:
        lines += [row(r) for r in upgrades]
    else:
        lines.append("Nothing — the builder already used every recommended card you own.")
    lines.append("")

    acquire = recs.get("acquire") or []
    lines.append(f"Worth acquiring ({len(acquire)})")
    lines.append(DIV)
    lines += [row(r) for r in acquire]
    basket = sum(r.price_usd or 0.0 for r in acquire)
    if basket:
        lines.append(f"{'':<34} {'':<7} {'':>13}   {'':<15} {_money(basket):>9}  total")

    lines.append("")
    lines.append("Role = Staple (60%+ of decks), Flex (25%+), Tech (below).")
    lines.append("Inclusion = share of this commander's EDHREC decks running the card.")
    lines.append("Synergy = how much more often it appears here than in decks generally.")
    return "\n".join(lines)


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


def format_bracket(bracket: dict) -> str:
    """The bracket estimate and the counts behind it, for the text output."""
    DIV = "─" * 40
    lines = [f"Commander bracket: {bracket['number']} — {bracket['name']}", DIV,
             _sentence(bracket["reason"]), ""]
    for crit in bracket["criteria"]:
        count = "?" if crit["unchecked"] else str(crit["count"])
        lines.append(f"  {crit['label']:<20} {count:>3}  {crit['note']}")
        if crit["cards"]:
            lines.append(f"  {'':<20} {'':>3}  {', '.join(crit['cards'])}")
    for note in bracket["notes"]:
        lines += ["", note]
    return "\n".join(lines)


def format_deck(commander: OwnedCard, deck: list, recs: dict | None = None,
                *, format_label: str = "Commander", name: str = "",
                bracket: dict | None = None) -> str:
    SEP = "═" * 60
    DIV = "─" * 40
    heading = f"  {format_label.upper()} DECK RECOMMENDATION"
    lines = [SEP, heading, SEP, ""]
    if name:
        lines += [name, ""]

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
    price = deck_price([commander] + deck)
    if price:
        lines.append(f"Paper value: {_money(price)}")
    if fillers_count:
        lines.append(f"Note: {fillers_count} basic land(s) added as filler — not from your collection.")

    if bracket:
        lines += ["", format_bracket(bracket)]

    if recs:
        lines += ["", format_recommendations(recs)]

    return "\n".join(lines)


def format_decklist(commander: OwnedCard, deck: list) -> str:
    """Plain importable deck list format (Moxfield/Archidekt compatible)."""
    lines = [f"1 {commander.name} *CMDR*", ""]
    for c in sorted(deck, key=_sort_key):
        lines.append(f"1 {c.name}")
    return "\n".join(lines)


def format_standard_deck(deck_entries: list, colors: set, sideboard: list | None = None,
                         *, format_label: str = "Standard", name: str = "") -> str:
    """Pretty-print a 60-card constructed deck (entries carry a count)."""
    SEP = "═" * 60
    DIV = "─" * 40
    color_str = "".join(c for c in "WUBRG" if c in colors) or "C"
    lines = [SEP, f"  {format_label.upper()} DECK RECOMMENDATION  [{color_str}]", SEP, ""]
    if name:
        lines += [name, ""]

    cats = _categorize([e.card for e in deck_entries])
    by_name = {e.card.name: e for e in deck_entries}
    total = 0
    for cat_name, cards in cats.items():
        if not cards:
            continue
        lines.append(f"{cat_name} ({sum(by_name[c.name].count for c in cards)})")
        lines.append(DIV)
        for c in sorted(cards, key=_sort_key):
            e = by_name[c.name]
            lines.append(f"{e.count} {c.name}")
            total += e.count
        lines.append("")

    if sideboard:
        side_total = sum(e.count for e in sideboard)
        lines.append(f"Sideboard ({side_total})")
        lines.append(DIV)
        for e in sorted(sideboard, key=lambda x: _sort_key(x.card)):
            lines.append(f"{e.count} {e.card.name}")
        lines.append("")

    lines.append(f"Total: {total} cards" + (f" + {sum(e.count for e in sideboard)} sideboard"
                                            if sideboard else ""))
    counts = {e.card.name: e.count for e in deck_entries + list(sideboard or [])}
    price = deck_price([e.card for e in deck_entries + list(sideboard or [])], counts)
    if price:
        lines.append(f"Paper value: {_money(price)}")
    return "\n".join(lines)


def format_standard_decklist(deck_entries: list, sideboard: list | None = None) -> str:
    """Arena-importable list: `N Card Name` per line, lands last."""
    def block(entries):
        ordered = sorted(entries, key=lambda e: ("Land" in e.card.type_line,
                                                 e.card.cmc, e.card.name))
        return "\n".join(f"{e.count} {e.card.name}" for e in ordered)

    main = block(deck_entries)
    if not sideboard:
        return main
    return f"{main}\n\nSideboard\n{block(sideboard)}"


def print_and_save_standard(deck_entries: list, colors: set, output_path: str,
                            sideboard: list | None = None, *,
                            format_label: str = "Standard", name: str = ""):
    pretty = format_standard_deck(deck_entries, colors, sideboard,
                                  format_label=format_label, name=name)
    print(pretty)
    Path(output_path).write_text(pretty, encoding="utf-8")
    import_path = Path(output_path).with_suffix(".decklist.txt")
    import_path.write_text(format_standard_decklist(deck_entries, sideboard), encoding="utf-8")
    print(f"\nSaved to: {output_path}")
    print(f"Arena import list: {import_path}")


def print_and_save(commander: OwnedCard, deck: list, output_path: str,
                   recs: dict | None = None, *,
                   format_label: str = "Commander", name: str = "",
                   bracket: dict | None = None):
    pretty = format_deck(commander, deck, recs, format_label=format_label, name=name,
                         bracket=bracket)
    importable = format_decklist(commander, deck)

    print(pretty)

    Path(output_path).write_text(pretty, encoding="utf-8")

    import_path = Path(output_path).with_suffix(".decklist.txt")
    import_path.write_text(importable, encoding="utf-8")

    print(f"\nSaved to: {output_path}")
    print(f"Import-ready list: {import_path}")
