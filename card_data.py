import json
import time
from pathlib import Path

import requests

CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "scryfall_default_cards.json"
CACHE_TTL = 7 * 86400  # 7 days — bulk data barely changes between set releases


def _is_cache_fresh() -> bool:
    if not CACHE_FILE.exists():
        return False
    return (time.time() - CACHE_FILE.stat().st_mtime) < CACHE_TTL


def _download_bulk_data():
    CACHE_DIR.mkdir(exist_ok=True)
    if _is_cache_fresh():
        return

    print("Fetching Scryfall bulk data index...")
    resp = requests.get(
        "https://api.scryfall.com/bulk-data/default_cards",
        headers={"User-Agent": "magic-builder/1.0"},
        timeout=15,
    )
    resp.raise_for_status()
    download_uri = resp.json()["download_uri"]

    print("Downloading card database (~250 MB, cached for 24 h)...")
    with requests.get(download_uri, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(CACHE_FILE, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {pct}% ({downloaded // 1_000_000} MB)", end="", flush=True)
    print("\nDownload complete.")


_scryfall_cache: tuple | None = None


def load_scryfall_lookup() -> tuple[dict, dict]:
    """Return (by_id, by_set_cn) built from a single file read, cached in-process."""
    global _scryfall_cache
    if _scryfall_cache is not None:
        return _scryfall_cache
    _download_bulk_data()
    print("Loading card database into memory...")
    with open(CACHE_FILE, encoding="utf-8") as f:
        cards = json.load(f)
    by_id = {c["id"]: c for c in cards}
    by_set_cn = {(c["set"].lower(), c["collector_number"].lower()): c for c in cards}
    _scryfall_cache = (by_id, by_set_cn)
    return _scryfall_cache


def enrich_collection(owned_cards: list, scryfall_lookup: dict):
    """Attach Scryfall metadata to each owned card in-place."""
    missing = 0
    for card in owned_cards:
        data = scryfall_lookup.get(card.scryfall_id)
        if not data:
            missing += 1
            continue
        front = data.get("card_faces", [{}])[0]  # MDFCs / adventures keep stats on the front face
        card.color_identity = data.get("color_identity", [])
        card.type_line = data.get("type_line", "")
        card.oracle_text = data.get("oracle_text") or front.get("oracle_text", "")
        card.keywords = data.get("keywords", [])
        card.cmc = data.get("cmc", 0.0)
        card.mana_cost = data.get("mana_cost") or front.get("mana_cost", "")
        card.power = data.get("power") or front.get("power", "")
        card.toughness = data.get("toughness") or front.get("toughness", "")
        card.rarity = data.get("rarity", "")
        card.legalities = data.get("legalities", {})
    if missing:
        print(f"  Warning: {missing} card(s) not found in Scryfall data (may be very new prints).")
