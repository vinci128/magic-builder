import gzip
import json
import time
from pathlib import Path

import requests

CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "scryfall_default_cards.jsonl.gz"
CACHE_TTL = 7 * 86400  # 7 days — bulk data barely changes between set releases

# Only the fields the builders read — keeping the raw objects costs several GB.
#
# Even slimmed, 116k cards held as plain dicts peak at ~644 MB: every card
# re-pays for a dict header, its own copy of each key string, and nested dicts
# for `legalities` and `image_uris`. So cards are stored as __slots__ objects
# (no per-card key strings, no hash table) that still answer to card["name"] /
# card.get(...), and repeated values are pooled so reprints share one object.
_PLAIN_FIELDS = (
    "id", "name", "set", "set_name", "collector_number", "color_identity",
    "type_line", "oracle_text", "keywords", "cmc", "mana_cost", "power",
    "toughness", "rarity",
)
# Derived keys whose stored form differs from what callers ask for.
_SLOTS = _PLAIN_FIELDS + ("legal_formats", "image_url", "face")
_PLAIN = frozenset(_PLAIN_FIELDS)
_FACE_KEEP = ("oracle_text", "mana_cost", "power", "toughness")
_MISSING = object()

# Values shared across printings (names, type lines, rules text, set codes...).
# Keys are the values themselves, so pooling costs a hash-table slot and saves
# every duplicate object behind it.
_pool: dict = {}


def _shared(value):
    """Return a single canonical instance of `value`, or None if it is empty."""
    if value is None or value == "":
        return None
    return _pool.setdefault(value, value)


class FaceView:
    """Front face of an MDFC / adventure — the only face the builders read."""

    __slots__ = ("oracle_text", "mana_cost", "power", "toughness", "image_url")

    def __init__(self, oracle_text, mana_cost, power, toughness, image_url):
        self.oracle_text = oracle_text
        self.mana_cost = mana_cost
        self.power = power
        self.toughness = toughness
        self.image_url = image_url

    def get(self, key, default=None):
        if key == "image_uris":
            return self.image_url or default
        value = getattr(self, key, None)
        return default if value is None else value

    def __getitem__(self, key):
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value


class CardView:
    """A Scryfall card stored compactly, read like the dict it replaced.

    `image_uris` reads back as the "normal" URL string rather than a dict —
    the only two call sites that touch it are adapted accordingly.
    """

    __slots__ = _SLOTS

    def get(self, key, default=None):
        if key in _PLAIN:
            value = getattr(self, key)
            return default if value is None else value
        if key == "legalities":
            return {fmt: "legal" for fmt in self.legal_formats}
        if key == "image_uris":
            return self.image_url or default
        if key == "card_faces":
            return [self.face] if self.face is not None else default
        return default

    def __getitem__(self, key):
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __contains__(self, key):
        return self.get(key, _MISSING) is not _MISSING

    def keys(self):
        return [k for k in _PLAIN_FIELDS if getattr(self, k) is not None]

    def values(self):
        return [getattr(self, k) for k in self.keys()]

    def items(self):
        return [(k, getattr(self, k)) for k in self.keys()]

    def __repr__(self):
        return f"<CardView {self.name!r} ({self.set}) {self.collector_number}>"


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


def _normal_url(images: dict | None) -> str | None:
    if not images:
        return None
    return images.get("normal") or images.get("small") or None


def _slim(card: dict) -> CardView:
    """Drop the ~40 fields nothing in this project reads, into a CardView."""
    view = CardView()
    view.id = card["id"]  # unique per card — pooling it would only cost memory
    view.name = _shared(card.get("name"))
    view.set = _shared(card.get("set"))
    view.set_name = _shared(card.get("set_name"))
    view.collector_number = _shared(card.get("collector_number"))
    view.color_identity = _shared(tuple(card.get("color_identity") or ()))
    view.type_line = _shared(card.get("type_line"))
    view.oracle_text = _shared(card.get("oracle_text"))
    view.keywords = _shared(tuple(card.get("keywords") or ()))
    view.cmc = _shared(card.get("cmc"))
    view.mana_cost = _shared(card.get("mana_cost"))
    view.power = _shared(card.get("power"))
    view.toughness = _shared(card.get("toughness"))
    view.rarity = _shared(card.get("rarity"))

    # Every consumer only ever asks whether a format is "legal", so the other
    # three states (not_legal / banned / restricted) need not be stored.
    legalities = card.get("legalities") or {}
    view.legal_formats = _shared(tuple(f for f, v in legalities.items() if v == "legal"))
    view.image_url = _normal_url(card.get("image_uris"))

    faces = card.get("card_faces")
    if faces:
        front = faces[0]
        view.face = FaceView(
            *(_shared(front.get(k)) for k in _FACE_KEEP),
            _normal_url(front.get("image_uris")),
        )
    else:
        view.face = None
    return view


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
        by_id[card.id] = card
        set_code = _shared((card.set or "").lower())
        number = _shared((card.collector_number or "").lower())
        by_set_cn[(set_code, number)] = card
    print(f"Loaded {len(by_id):,} cards.")
    _pool.clear()  # the values stay alive on the cards; the lookup table need not
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
        # Collection-side fields stay plain lists/dicts; the compact tuples are
        # a storage detail of the shared card database.
        card.color_identity = list(data.get("color_identity", ()))
        card.type_line = data.get("type_line", "")
        card.oracle_text = data.get("oracle_text") or front.get("oracle_text", "")
        card.keywords = list(data.get("keywords", ()))
        card.cmc = data.get("cmc", 0.0)
        card.mana_cost = data.get("mana_cost") or front.get("mana_cost", "")
        card.power = data.get("power") or front.get("power", "")
        card.toughness = data.get("toughness") or front.get("toughness", "")
        card.rarity = data.get("rarity", "")
        card.legalities = data.get("legalities", {})
        card.image_url = data.get("image_uris") or front.get("image_uris") or ""
    if missing:
        print(f"  Warning: {missing} card(s) not found in Scryfall data (may be very new prints).")
