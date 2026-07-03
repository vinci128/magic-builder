import csv
import re
from collection import OwnedCard, parse_collection

_LINE_RE = re.compile(r'^(\d+)\s+(.+?)\s+\(([A-Za-z0-9]+)\)\s+(\S+)$')


def parse_arena_collection(path: str, scryfall_by_set_cn: dict) -> list:
    """Parse an Arena collection/deck export (`N Card Name (SET) ###`) into OwnedCard objects."""
    cards: dict[str, OwnedCard] = {}
    missing: list[str] = []

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
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


# Arena uses a few set codes that differ from Scryfall's
_ARENA_SET_ALIASES = {"dar": "dom", "conf": "con"}


def parse_arena_log_csv(path: str, scryfall_by_set_cn: dict) -> list:
    """Parse a CSV scraped from the Arena Player.log
    (columns: grp_id,name,set,collector_number,quantity,...)."""
    cards: dict[str, OwnedCard] = {}
    missing: list[str] = []

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            set_code = (row.get("set") or "").strip().lower()
            set_code = _ARENA_SET_ALIASES.get(set_code, set_code)
            cn = (row.get("collector_number") or "").strip().lower()
            name = (row.get("name") or "").strip()
            try:
                qty = int(row.get("quantity") or 1)
            except ValueError:
                qty = 1
            if not set_code or not cn:
                continue

            data = scryfall_by_set_cn.get((set_code, cn))
            if not data:
                missing.append(f"{name} ({set_code.upper()}) {cn}")
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
    """Return 'manabox', 'arena_log', or 'arena' by inspecting the first line of the file."""
    with open(path, encoding="utf-8") as f:
        first_line = f.readline().strip()
    if "Scryfall ID" in first_line or "Binder Name" in first_line:
        return "manabox"
    if "grp_id" in first_line and "collector_number" in first_line:
        return "arena_log"
    return "arena"


def load_owned_cards(path: str, scryfall_by_set_cn: dict) -> list:
    """Parse a collection file in ManaBox CSV, Arena export, or Arena log-CSV format."""
    fmt = detect_collection_format(path)
    if fmt == "arena_log":
        return parse_arena_log_csv(path, scryfall_by_set_cn)
    if fmt == "arena":
        return parse_arena_collection(path, scryfall_by_set_cn)
    return parse_collection(path)
