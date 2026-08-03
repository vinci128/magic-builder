#!/usr/bin/env python3
"""Scrape owned cards from MTG Arena's Player.log into collection_from_logs.csv.

Arena no longer writes the full collection to Player.log, but with
"Detailed Logs (Plugin Support)" enabled it logs an account payload
(InventoryInfo + Decks) on login. Every card in one of your decks —
including starter/precon decks — is a card you own, so the union of all
deck contents (max quantity per card across decks) is the best owned-card
snapshot available from the logs.

Card ids are resolved to name/set/collector number via Arena's own card
database (Raw_CardDatabase_*.mtga, SQLite).

Intended to run automatically via the mtga-collection systemd user units;
safe to run by hand at any time.
"""

import csv
import glob
import io
import json
import sqlite3
import sys
from pathlib import Path

MTGA_LOG_DIR = Path(
    "/home/vinci/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c"
    "/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA"
)
CARD_DB_GLOB = (
    "/home/vinci/.local/share/Steam/steamapps/common/MTGA"
    "/MTGA_Data/Downloads/Raw/Raw_CardDatabase_*.mtga"
)
OUTPUT_CSV = Path(__file__).resolve().parent / "collection_from_logs.csv"

DECK_ZONES = ("MainDeck", "Sideboard", "CommandZone", "Companions")


def find_latest_account_payload() -> dict | None:
    """Return the most recent InventoryInfo/Decks payload from the Arena logs."""
    for log in (MTGA_LOG_DIR / "Player.log", MTGA_LOG_DIR / "Player-prev.log"):
        if not log.exists():
            continue
        last = None
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"InventoryInfo"' in line and '"Decks"' in line:
                    last = line
        if last is None:
            continue
        try:
            return json.loads(last[last.index("{"):])
        except (ValueError, json.JSONDecodeError):
            continue
    return None


def owned_cards_from_payload(payload: dict) -> dict[int, int]:
    """Max quantity per card id across all decks in the account payload."""
    cards: dict[int, int] = {}
    for deck in payload.get("Decks", {}).values():
        for zone in DECK_ZONES:
            for entry in deck.get(zone) or []:
                cid, qty = entry["cardId"], entry["quantity"]
                if qty > cards.get(cid, 0):
                    cards[cid] = qty
    return cards


def load_card_db() -> dict[int, dict]:
    paths = glob.glob(CARD_DB_GLOB)
    if not paths:
        sys.exit(f"No Arena card database found at {CARD_DB_GLOB}")
    db = sqlite3.connect(max(paths, key=lambda p: Path(p).stat().st_mtime))
    # Not every LocId has a Formatted=0 row; ordering DESC makes the plain
    # variant (lowest Formatted) win where it exists.
    names = {
        loc_id: name
        for loc_id, name in db.execute(
            "SELECT LocId, Loc FROM Localizations_enUS ORDER BY Formatted DESC"
        )
    }
    cards = {}
    rows = db.execute(
        "SELECT GrpId, TitleId, ExpansionCode, CollectorNumber,"
        "       ColorIdentity, Types, Rarity, IsToken FROM Cards"
    )
    for grp_id, title_id, set_code, cn, color_id, types, rarity, is_token in rows:
        if is_token:
            continue
        cards[grp_id] = {
            "name": names.get(title_id, ""),
            "set": set_code or "",
            "collector_number": cn or "",
            "color_identity": color_id or "",
            "types": types or "",
            "rarity": rarity,
        }
    db.close()
    return cards


def main() -> None:
    payload = find_latest_account_payload()
    if payload is None:
        sys.exit(
            "No account payload found in Player.log or Player-prev.log.\n"
            "Launch Arena with Detailed Logs (Plugin Support) enabled, then retry."
        )

    owned = owned_cards_from_payload(payload)
    card_db = load_card_db()

    rows, unknown = [], 0
    for grp_id, qty in owned.items():
        info = card_db.get(grp_id)
        if info is None or not info["name"]:
            unknown += 1
            continue
        rows.append({"grp_id": grp_id, "quantity": qty, **info})
    rows.sort(key=lambda r: (r["name"], r["grp_id"]))

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "grp_id", "name", "set", "collector_number",
            "quantity", "color_identity", "types", "rarity",
        ],
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    content = buf.getvalue()

    if OUTPUT_CSV.exists() and OUTPUT_CSV.read_text(encoding="utf-8") == content:
        print(f"{OUTPUT_CSV.name}: unchanged ({len(rows)} cards)")
        return
    OUTPUT_CSV.write_text(content, encoding="utf-8")
    skipped = f", {unknown} unknown ids skipped" if unknown else ""
    print(f"{OUTPUT_CSV.name}: wrote {len(rows)} cards{skipped}")


if __name__ == "__main__":
    main()
