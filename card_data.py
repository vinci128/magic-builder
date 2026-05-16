import json
import time
from pathlib import Path

import requests

CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "scryfall_default_cards.json"
CACHE_TTL = 86400  # 24 hours


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


def load_scryfall_lookup() -> dict:
    _download_bulk_data()
    print("Loading card database into memory...")
    with open(CACHE_FILE, encoding="utf-8") as f:
        cards = json.load(f)
    return {card["id"]: card for card in cards}


def enrich_collection(owned_cards: list, scryfall_lookup: dict):
    """Attach Scryfall metadata to each owned card in-place."""
    missing = 0
    for card in owned_cards:
        data = scryfall_lookup.get(card.scryfall_id)
        if not data:
            missing += 1
            continue
        card.color_identity = data.get("color_identity", [])
        card.type_line = data.get("type_line", "")
        # card_faces[0] oracle_text for MDFCs / adventures
        if "oracle_text" in data:
            card.oracle_text = data["oracle_text"]
        elif "card_faces" in data:
            card.oracle_text = data["card_faces"][0].get("oracle_text", "")
        card.keywords = data.get("keywords", [])
        card.cmc = data.get("cmc", 0.0)
        card.legalities = data.get("legalities", {})
    if missing:
        print(f"  Warning: {missing} card(s) not found in Scryfall data (may be very new prints).")
