import csv
from dataclasses import dataclass, field


@dataclass
class OwnedCard:
    name: str
    scryfall_id: str
    quantity: int
    foil: bool = False
    set_name: str = ""
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
    is_basic_filler: bool = False  # True when added as basic land padding


def parse_collection(csv_path: str) -> list:
    cards: dict[str, OwnedCard] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row.get("Scryfall ID", "").strip()
            if not sid:
                continue
            qty = int(row.get("Quantity", 1))
            if sid in cards:
                cards[sid].quantity += qty
            else:
                cards[sid] = OwnedCard(
                    name=row["Name"].strip(),
                    scryfall_id=sid,
                    quantity=qty,
                    foil=row.get("Foil", "normal").strip().lower() == "foil",
                    set_name=row.get("Set name", "").strip(),
                )
    return list(cards.values())
