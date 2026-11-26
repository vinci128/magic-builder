import csv
import re
import warnings

from magic_builder.collection.manabox import (
    OwnedCard,
    _report_unusable,
    parse_collection,
)

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
                    set_code=data.get("set", ""),
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
    unusable: list[str] = []

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
                # Without both halves of the key there is nothing to look up. Reported, not swallowed:
                # a partial row usually means the scrape upstream went wrong, which the user should know.
                unusable.append(f"{name or '?'} (missing "
                                f"{'set' if not set_code else 'collector number'}; quantity {qty})")
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
                    set_code=data.get("set", ""),
                )

    _report_unusable("card(s) not matched in Scryfall data", missing)
    _report_unusable("row(s) skipped for missing set or collector number", unusable)
    return list(cards.values())


def detect_collection_format(path: str) -> str:
    """Return 'manabox', 'arena_log', 'arena', or 'unknown' by inspecting the first line.

    'unknown' is a real answer. This used to fall back to 'arena', which meant a file in no known format
    was confidently parsed as an Arena export and produced an empty collection with no complaint — the
    worst way to fail, because "you own no cards" looks like a fact.
    """
    # The first *non-empty* line: a leading blank line is common in hand-edited exports, and treating
    # it as the header would refuse a perfectly good file.
    first_line = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                first_line = line.strip()
                break
    if "Scryfall ID" in first_line or "Binder Name" in first_line:
        return "manabox"
    if "grp_id" in first_line and "collector_number" in first_line:
        return "arena_log"
    if _LINE_RE.match(first_line) or first_line in ("Deck", "Sideboard", "Commander", "Companion"):
        return "arena"
    return "unknown"


def load_owned_cards(path: str, scryfall_by_set_cn: dict) -> list:
    """Parse a collection file in ManaBox CSV, Arena export, or Arena log-CSV format.

    A file in none of those formats is refused with a warning rather than parsed as something it is not:
    returning an empty collection silently would read as "you own no cards".
    """
    fmt = detect_collection_format(path)
    if fmt == "arena_log":
        return parse_arena_log_csv(path, scryfall_by_set_cn)
    if fmt == "arena":
        return parse_arena_collection(path, scryfall_by_set_cn)
    if fmt == "manabox":
        return parse_collection(path)
    warnings.warn(
        f"{path}: not recognised as a ManaBox CSV, an Arena export, or an Arena log CSV — "
        f"no cards read. Supported headers: 'Scryfall ID' or 'Binder Name' (ManaBox), "
        f"'grp_id'+'collector_number' (Arena log), or lines like '1 Sol Ring (TRC) 499' (Arena export).",
        UserWarning, stacklevel=2,
    )
    return []
