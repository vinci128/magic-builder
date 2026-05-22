import re
from collection import OwnedCard

_LINE_RE = re.compile(r'^(\d+)\s+(.+?)\s+\(([A-Za-z0-9]+)\)\s+(\S+)$')
_SECTION_RE = re.compile(r'^[A-Za-z][A-Za-z ]*$')


def parse_arena_collection(path: str, scryfall_by_set_cn: dict) -> list:
    """Parse an Arena collection/deck export (`N Card Name (SET) ###`) into OwnedCard objects."""
    cards: dict[str, OwnedCard] = {}
    missing: list[str] = []

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            # Skip Arena section headers like "Deck", "Sideboard", "Commander"
            if _SECTION_RE.match(line):
                continue
            m = _LINE_RE.match(line)
            if not m:
                continue
            qty_str, name, set_code, cn = m.groups()
            qty = int(qty_str)

            key = (set_code.lower(), cn.lower())
            data = scryfall_by_set_cn.get(key)
            if not data:
                missing.append(f"{name} ({set_code}) {cn}")
                continue

            sid = data["id"]
            if sid in cards:
                cards[sid].quantity += qty
            else:
                cards[sid] = OwnedCard(
                    name=data["name"],
                    scryfall_id=sid,
                    quantity=qty,
                    set_name=data.get("set_name", ""),
                )

    if missing:
        print(f"  Warning: {len(missing)} card(s) not matched in Scryfall data:")
        for entry in missing[:5]:
            print(f"    {entry}")
        if len(missing) > 5:
            print(f"    ... and {len(missing) - 5} more")

    return list(cards.values())


def detect_collection_format(path: str) -> str:
    """Return 'manabox' or 'arena' by inspecting the first line of the file."""
    with open(path, encoding="utf-8") as f:
        first_line = f.readline().strip()
    if "Scryfall ID" in first_line or "Binder Name" in first_line:
        return "manabox"
    return "arena"
