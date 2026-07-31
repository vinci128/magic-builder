import gzip
import json
import time
from pathlib import Path

import requests

CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "scryfall_default_cards.jsonl.gz"
CACHE_TTL = 7 * 86400  # 7 days — bulk data barely changes between set releases

# Only the fields the builders read — keeping the raw objects costs several GB.
_KEEP = (
    "id", "name", "set", "set_name", "collector_number", "color_identity",
    "type_line", "oracle_text", "keywords", "cmc", "mana_cost", "power",
    "toughness", "rarity", "legalities",
)
_FACE_KEEP = ("oracle_text", "mana_cost", "power", "toughness")


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
    index = resp.json()
    # Scryfall serves gzipped JSONL now; older responses carried a plain JSON array.
    download_uri = index.get("jsonl_download_uri") or index.get("download_uri")
    if not download_uri:
        raise RuntimeError("Scryfall bulk-data index has no download URI")

    print("Downloading card database (~80 MB compressed, cached for 7 days)...")
    tmp_path = CACHE_FILE.with_suffix(".part")
    with requests.get(download_uri, stream=True, timeout=300) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {pct}% ({downloaded // 1_000_000} MB)", end="", flush=True)
    tmp_path.replace(CACHE_FILE)
    print("\nDownload complete.")


def _slim(card: dict) -> dict:
    """Drop the ~40 fields nothing in this project reads."""
    out = {k: card[k] for k in _KEEP if k in card}
    images = card.get("image_uris")
    if images:
        out["image_uris"] = {"normal": images.get("normal") or images.get("small", "")}
    faces = card.get("card_faces")
    if faces:
        front = faces[0]
        face = {k: front[k] for k in _FACE_KEEP if k in front}
        face_images = front.get("image_uris")
        if face_images:
            face["image_uris"] = {"normal": face_images.get("normal") or face_images.get("small", "")}
        out["card_faces"] = [face]
    return out


def _iter_cards():
    """Yield slimmed card dicts from the cached bulk file (JSONL, or legacy JSON array)."""
    opener = gzip.open if CACHE_FILE.suffix == ".gz" else open
    with opener(CACHE_FILE, "rt", encoding="utf-8") as f:
        first = f.read(1)
        f.seek(0)
        if first == "[":  # legacy single-array dump
            for card in json.load(f):
                yield _slim(card)
            return
        for line in f:
            line = line.strip().rstrip(",")
            if line and line not in ("[", "]"):
                yield _slim(json.loads(line))


_scryfall_cache: tuple | None = None


def load_scryfall_lookup() -> tuple[dict, dict]:
    """Return (by_id, by_set_cn) built from a single file read, cached in-process."""
    global _scryfall_cache
    if _scryfall_cache is not None:
        return _scryfall_cache
    _download_bulk_data()
    print("Loading card database into memory...")
    by_id: dict = {}
    by_set_cn: dict = {}
    for card in _iter_cards():
        by_id[card["id"]] = card
        by_set_cn[(card["set"].lower(), card["collector_number"].lower())] = card
    print(f"Loaded {len(by_id):,} cards.")
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
        images = data.get("image_uris") or front.get("image_uris") or {}
        card.image_url = images.get("normal") or images.get("small", "")
    if missing:
        print(f"  Warning: {missing} card(s) not found in Scryfall data (may be very new prints).")
