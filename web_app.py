"""FastAPI web UI for the deck builder.

Run with:  python web_app.py   (or: uvicorn web_app:app --reload)

The Scryfall bulk database is loaded once in a background thread at startup;
uploads are parsed into an in-memory session so several decks can be built
from the same collection without re-parsing it.
"""

import contextlib
import os
import re
import tempfile
import threading
import uuid
from collections import Counter, OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from arena_collection import detect_collection_format, load_owned_cards
from card_data import load_scryfall_lookup, enrich_collection
from commander import find_commanders
from deck_builder import build_deck
from edhrec_recs import recommend, _name_key as _edhrec_name_key
from standard_builder import build_standard_deck
from output import (
    format_deck,
    format_decklist,
    format_standard_deck,
    format_standard_decklist,
)

STATIC_DIR = Path(__file__).parent / "web"
MAX_SESSIONS = 20
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# ── Card database warm-up ────────────────────────────────────────────────────

_db_state = {"ready": False, "error": ""}
_sessions_lock = threading.Lock()


def _warm_card_db():
    try:
        load_scryfall_lookup()
        _db_state["ready"] = True
    except Exception as exc:  # network failure, disk full, ...
        _db_state["error"] = str(exc)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warm_card_db, daemon=True).start()
    yield


app = FastAPI(title="Magic Deck Builder", lifespan=lifespan)


def _require_db():
    if _db_state["error"]:
        raise HTTPException(503, f"Card database unavailable: {_db_state['error']}")
    if not _db_state["ready"]:
        raise HTTPException(503, "Card database is still loading. Try again in a moment.")
    return load_scryfall_lookup()


# ── Session store ────────────────────────────────────────────────────────────

_sessions: "OrderedDict[str, dict]" = OrderedDict()


def _store(session: dict) -> str:
    sid = uuid.uuid4().hex
    with _sessions_lock:
        _sessions[sid] = session
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
    return sid


def _session(sid: str) -> dict:
    session = _sessions.get(sid)
    if not session:
        raise HTTPException(404, "Collection not found — upload it again.")
    return session


# ── Card / deck serialization ────────────────────────────────────────────────

CATEGORY_ORDER = [
    "Creatures", "Planeswalkers", "Instants", "Sorceries",
    "Enchantments", "Artifacts", "Lands", "Other",
]

_SYMBOL_RE = re.compile(r"\{([^}]+)\}")


def _category(card) -> str:
    tl = card.type_line
    for needle, cat in (
        ("Creature", "Creatures"), ("Planeswalker", "Planeswalkers"),
        ("Instant", "Instants"), ("Sorcery", "Sorceries"),
        ("Enchantment", "Enchantments"), ("Artifact", "Artifacts"),
        ("Land", "Lands"),
    ):
        if needle in tl:
            return cat
    return "Other"


def _card_json(card, count: int) -> dict:
    return {
        "name": card.name,
        "count": count,
        "cmc": int(card.cmc),
        "type_line": card.type_line,
        "mana_cost": card.mana_cost,
        "colors": card.color_identity,
        "rarity": card.rarity,
        "image_url": card.image_url,
        "oracle_text": card.oracle_text,
        "is_filler": card.is_basic_filler,
    }


def _pip_demand(pairs: list) -> dict:
    """Coloured mana pips required by the deck, keyed W/U/B/R/G."""
    demand = Counter()
    for card, count in pairs:
        if "Land" in card.type_line:
            continue
        for sym in _SYMBOL_RE.findall(card.mana_cost or ""):
            for color in "WUBRG":
                if color in sym:
                    demand[color] += count
    return {c: demand.get(c, 0) for c in "WUBRG" if demand.get(c)}


def _curve(pairs: list) -> list:
    """Non-land card counts per mana value, 0..7+."""
    buckets = [0] * 8
    for card, count in pairs:
        if "Land" in card.type_line:
            continue
        buckets[min(int(card.cmc), 7)] += count
    return buckets


def _deck_payload(pairs: list, *, colors: set, pretty: str, decklist: str,
                  commander=None) -> dict:
    """pairs: list of (OwnedCard, count) for the main deck."""
    grouped: dict[str, list] = {}
    for card, count in pairs:
        grouped.setdefault(_category(card), []).append(_card_json(card, count))
    for cards in grouped.values():
        cards.sort(key=lambda c: (c["is_filler"], c["cmc"], c["name"]))

    categories = [
        {"name": name, "count": sum(c["count"] for c in grouped[name]), "cards": grouped[name]}
        for name in CATEGORY_ORDER if name in grouped
    ]
    total = sum(count for _, count in pairs) + (1 if commander else 0)
    return {
        "commander": _card_json(commander, 1) if commander else None,
        "colors": [c for c in "WUBRG" if c in colors] or ["C"],
        "categories": categories,
        "total": total,
        "curve": _curve(pairs),
        "pips": _pip_demand(pairs),
        "pretty": pretty,
        "decklist": decklist,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    return {
        "card_db_ready": _db_state["ready"],
        "card_db_error": _db_state["error"],
    }


@app.post("/api/collection")
async def upload_collection(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Collection file is larger than 25 MB.")
    if not raw.strip():
        raise HTTPException(400, "That file is empty.")

    lookup, by_set_cn = _require_db()

    suffix = Path(file.filename or "collection.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        fmt = detect_collection_format(tmp_path)
        owned = load_owned_cards(tmp_path, by_set_cn)
    except UnicodeDecodeError:
        raise HTTPException(400, "That file isn't text — export a ManaBox CSV or an Arena list.")
    except Exception as exc:
        raise HTTPException(400, f"Could not read that collection: {exc}")
    finally:
        os.unlink(tmp_path)

    if not owned:
        raise HTTPException(
            400,
            "No cards matched. Supported: ManaBox CSV, Arena deck export "
            "(`4 Card Name (SET) 123`), or an Arena collection CSV.",
        )

    enrich_collection(owned, lookup)
    sid = _store({"owned": owned, "commanders": None, "filename": file.filename})

    colors = Counter()
    types = Counter()
    rarities = Counter()
    for card in owned:
        for color in card.color_identity:
            colors[color] += card.quantity
        if not card.color_identity and "Land" not in card.type_line:
            colors["C"] += card.quantity
        types[_category(card)] += card.quantity
        if card.rarity:
            rarities[card.rarity] += card.quantity

    return {
        "id": sid,
        "filename": file.filename,
        "source_format": {"manabox": "ManaBox CSV", "arena": "Arena list",
                          "arena_log": "Arena collection CSV"}.get(fmt, fmt),
        "unique": len(owned),
        "total": sum(c.quantity for c in owned),
        "colors": {c: colors.get(c, 0) for c in "WUBRGC" if colors.get(c)},
        "types": {t: types[t] for t in CATEGORY_ORDER if types.get(t)},
        "rarities": dict(rarities),
        "commander_legal": sum(1 for c in owned if c.legalities.get("commander") == "legal"),
        "standard_legal": sum(1 for c in owned if c.legalities.get("standard") == "legal"),
    }


@app.get("/api/collection/{sid}/commanders")
def list_commanders(sid: str, limit: int = 24, edhrec: bool = False):
    session = _session(sid)
    cache_key = "commanders_edhrec" if edhrec else "commanders"
    ranked = session.get(cache_key)
    if ranked is None:
        ranked = find_commanders(session["owned"], use_popularity=edhrec)
        session[cache_key] = ranked
    return {
        "commanders": [
            {**_card_json(card, 1), "score": int(score)}
            for card, score in ranked[:limit]
        ],
        "total": len(ranked),
    }


class CommanderDeckRequest(BaseModel):
    commander_name: str | None = None


@app.post("/api/collection/{sid}/deck/commander")
def build_commander_deck(sid: str, req: CommanderDeckRequest):
    session = _session(sid)
    owned = session["owned"]

    if req.commander_name:
        commander = next((c for c in owned if c.name == req.commander_name), None)
        if commander is None:
            raise HTTPException(404, f"{req.commander_name} isn't in this collection.")
    else:
        ranked = session.get("commanders") or find_commanders(owned, use_popularity=False)
        session["commanders"] = ranked
        if not ranked:
            raise HTTPException(422, "No Commander-legal legendary creatures in this collection.")
        commander = ranked[0][0]

    deck = build_deck(commander, owned)
    # Kept so /recommendations can diff EDHREC's list against what was actually built.
    session.setdefault("decks", {})[commander.name] = deck

    pairs = [(card, 1) for card in deck]
    return _deck_payload(
        pairs,
        colors=set(commander.color_identity),
        pretty=format_deck(commander, deck),
        decklist=format_decklist(commander, deck),
        commander=commander,
    )


class StandardDeckRequest(BaseModel):
    colors: str | None = None


@app.post("/api/collection/{sid}/deck/standard")
def build_standard(sid: str, req: StandardDeckRequest):
    session = _session(sid)
    forced = set(req.colors.upper()) if req.colors else None
    if forced and not forced.issubset(set("WUBRG")):
        raise HTTPException(400, "Colors must be any of W, U, B, R, G.")

    try:
        entries, colors = build_standard_deck(session["owned"], colors=forced)
    except Exception as exc:
        raise HTTPException(422, f"Could not build a Standard deck: {exc}")
    if not entries:
        raise HTTPException(422, "Not enough Standard-legal cards in this collection.")

    pairs = [(e.card, e.count) for e in entries]
    return _deck_payload(
        pairs,
        colors=colors,
        pretty=format_standard_deck(entries, colors),
        decklist=format_standard_decklist(entries),
    )


# ── EDHREC recommendations ───────────────────────────────────────────────────

_name_index: dict[str, dict] | None = None
_name_index_lock = threading.Lock()


def _scryfall_by_name() -> dict:
    """Name-keyed view of the bulk data, built once — EDHREC gives names, not ids."""
    global _name_index
    if _name_index is None:
        with _name_index_lock:
            if _name_index is None:
                lookup, _ = load_scryfall_lookup()
                index: dict[str, dict] = {}
                for card in lookup.values():
                    index.setdefault(_edhrec_name_key(card["name"]), card)
                _name_index = index
    return _name_index


def _rec_json(rec) -> dict:
    """Serialize a Recommendation; fill card details from the collection or Scryfall."""
    card = rec.owned
    data = _scryfall_by_name().get(_edhrec_name_key(rec.name)) if card is None else None
    # CardView stores image_uris as the "normal" URL string, not a dict.
    image_url = data.get("image_uris", "") if data is not None else ""
    return {
        "name": rec.name,
        "category": rec.category,
        "synergy": round(rec.synergy, 3),
        "inclusion": round(rec.inclusion, 3),
        "num_decks": rec.num_decks,
        "owned": card is not None,
        "type_line": card.type_line if card else (data.get("type_line", "") if data else ""),
        "mana_cost": card.mana_cost if card else (data.get("mana_cost", "") if data else ""),
        "image_url": card.image_url if card else image_url,
    }


@app.get("/api/collection/{sid}/recommendations")
def recommendations(sid: str, commander: str, limit: int = 20, refresh: bool = False):
    """EDHREC's card list for a commander, split into owned upgrades and cards to acquire."""
    session = _session(sid)
    deck = (session.get("decks") or {}).get(commander)
    if deck is None:
        raise HTTPException(409, "Build the deck first — recommendations are relative to it.")

    commander_card = next((c for c in session["owned"] if c.name == commander), None)
    if commander_card is None:
        raise HTTPException(404, f"{commander} isn't in this collection.")

    result = recommend(commander_card, session["owned"], deck, limit=limit, refresh=refresh)
    return {
        "commander": commander,
        "error": result.get("error", ""),
        "in_deck": result["in_deck"],
        "total": result["total"],
        "upgrades": [_rec_json(r) for r in result["upgrades"]],
        "acquire": [_rec_json(r) for r in result["acquire"]],
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
