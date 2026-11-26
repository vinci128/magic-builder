import csv
from dataclasses import dataclass, field


@dataclass
class OwnedCard:
    name: str
    scryfall_id: str
    quantity: int
    foil: bool = False
    set_name: str = ""
    set_code: str = ""  # lowercase Scryfall set code of this printing, when the source has it
    # ManaBox binder/deck name -> copies of this printing in it; how precons.py
    # tells a card already sleeved in another deck from a loose one.
    binders: dict = field(default_factory=dict)
    # Enriched from Scryfall
    color_identity: list = field(default_factory=list)
    type_line: str = ""
    oracle_text: str = ""
    keywords: list = field(default_factory=list)
    cmc: float = 0.0
    mana_cost: str = ""
    power: str = ""
    toughness: str = ""
    rarity: str = ""
    legalities: dict = field(default_factory=dict)
    image_url: str = ""
    price_usd: float = 0.0  # non-foil USD for this printing, 0.0 when unpriced
    game_changer: bool = False  # on WotC's Game Changer list (see brackets.py)
    is_basic_filler: bool = False  # True when added as basic land padding


def _report_unusable(kind: str, entries: list[str]) -> None:
    """Say which rows were dropped, and how many.

    Dropping a row is sometimes unavoidable — it cannot be matched, or it is missing the field the
    lookup needs. Dropping it *silently* is not: the user's collection ends up quietly short, the deck
    builder reports that they do not own a card they are holding, and nothing anywhere says why. The
    count matters as much as the names, because it is the difference between "one odd line" and "this
    export is not the format I thought".
    """
    if not entries:
        return
    print(f"  Warning: {len(entries)} {kind}:")
    for entry in entries[:5]:
        print(f"    {entry}")
    if len(entries) > 5:
        print(f"    ... and {len(entries) - 5} more")


def parse_collection(csv_path: str) -> list:
    cards: dict[str, OwnedCard] = {}
    unusable: list[str] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = (row.get("Scryfall ID") or "").strip()
            if not sid:
                # No id means no way to match the card against Scryfall or a deck, so it cannot be used.
                # Named in the report rather than skipped in silence.
                unusable.append(f"{row.get('Name', '?').strip() or '?'} "
                                f"(no Scryfall ID; quantity {row.get('Quantity', '?')})")
                continue
            qty = int(row.get("Quantity", 1))
            binder = (row.get("Binder Name") or "").strip()
            if sid in cards:
                cards[sid].quantity += qty
            else:
                cards[sid] = OwnedCard(
                    name=row["Name"].strip(),
                    scryfall_id=sid,
                    quantity=qty,
                    foil=row.get("Foil", "normal").strip().lower() == "foil",
                    set_name=row.get("Set name", "").strip(),
                    set_code=(row.get("Set code") or "").strip().lower(),
                )
            if binder:
                cards[sid].binders[binder] = cards[sid].binders.get(binder, 0) + qty
    _report_unusable("card(s) skipped for having no Scryfall ID", unusable)
    return list(cards.values())
