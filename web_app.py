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
from pydantic import BaseModel, Field

import brackets
import combos
import crispi
import formats
from arena_collection import detect_collection_format, load_owned_cards
from card_data import load_scryfall_lookup, load_by_name, enrich_collection, name_key
from commander import find_commanders
from deck_builder import (
    build_deck,
    deck_archetype as commander_archetype,
    synergy_reasons,
    SYNERGY_THEME_LABELS,
    GENERIC_SYNERGY_THEMES,
)
from edhrec_recs import recommend
from standard_builder import (
    build_standard_deck,
    deck_archetype as constructed_archetype,
    synergy_tags as standard_synergy_tags,
    TAG_LABELS as STANDARD_TAG_LABELS,
)
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


def _card_json(card, count: int, reasons: list | None = None) -> dict:
    payload = {
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
        "price": round(card.price_usd or 0.0, 2),
    }
    if reasons is not None:
        # Strongest pull first, so a truncated list still shows the real reason.
        ranked = sorted(reasons, key=lambda r: abs(r.points), reverse=True)
        payload["synergy"] = round(sum(r.points for r in reasons), 1)
        payload["synergy_why"] = [r.label for r in ranked[:3]]
    return payload


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


def _commander_synergy(pairs: list, commander) -> tuple[dict, dict]:
    """Summarise how the deck hangs together, plus per-card reasons by name."""
    by_name: dict[str, list] = {}
    themes = Counter()
    theme_points = Counter()
    tribal_labels = Counter()
    scored: list[tuple[float, object]] = []
    linked = 0
    drags: list[dict] = []

    for card, _ in pairs:
        if card.is_basic_filler:
            continue
        reasons = synergy_reasons(card, commander)
        by_name[card.name] = reasons
        total = sum(r.points for r in reasons)
        scored.append((total, card))

        real = [r for r in reasons if r.points > 0 and r.key not in GENERIC_SYNERGY_THEMES]
        if real:
            linked += 1
        for reason in real:
            themes[reason.key] += 1
            theme_points[reason.key] += reason.points
            if reason.key == "tribal":
                tribal_labels[reason.label] += 1
        penalties = [r for r in reasons if r.points < 0]
        if penalties:
            drags.append({"name": card.name, "label": penalties[0].label})

    considered = len(scored)
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))

    # Ranked by how hard each term actually pulled on card selection, not by how
    # many cards it touched: one shared creature type is worth 3.0 against a
    # keyword's 1.0, so counting alone would rank a broad weak term over a
    # narrow decisive one.
    theme_list = []
    for key, points in sorted(theme_points.items(), key=lambda kv: -kv[1]):
        label = SYNERGY_THEME_LABELS.get(key, key)
        if key == "tribal" and tribal_labels:
            label = tribal_labels.most_common(1)[0][0]
        theme_list.append({
            "label": label,
            "count": themes[key],
            "weight": round(points, 1),
        })

    return {
        "kind": "commander",
        "subject": commander.name,
        "linked": linked,
        "considered": considered,
        "headline": (
            f"{linked} of the {considered} cards drawn from your collection share something "
            f"with {commander.name} beyond colour."
        ) if considered else "",
        "themes": theme_list[:6],
        "top": [
            {
                "name": card.name,
                "score": round(total, 1),
                "why": [r.label for r in sorted(by_name[card.name],
                                                key=lambda r: abs(r.points), reverse=True)[:2]],
            }
            for total, card in scored[:6] if total > 0
        ],
        "drags": drags[:4],
    }, by_name


def _standard_synergy(pairs: list) -> dict:
    """Summarise the tag pairings the Standard builder rewarded."""
    counts = Counter()
    for card, count in pairs:
        for tag in standard_synergy_tags(card):
            counts[tag] += count

    # The Standard builder has no per-term weights, so copies played is the
    # only ranking available — weight mirrors count to keep the payload uniform.
    themes = [
        {"label": STANDARD_TAG_LABELS.get(tag, tag), "count": count, "weight": count}
        for tag, count in counts.most_common()
    ]
    pairings = [
        f"{counts[source]} {STANDARD_TAG_LABELS[source].lower()} feeding "
        f"{counts[payoff]} {STANDARD_TAG_LABELS[payoff].lower()}"
        for source, payoff in (("lifegain_source", "lifegain_payoff"),
                               ("token_source", "token_payoff"))
        if counts[source] and counts[payoff]
    ]
    return {
        "kind": "standard",
        "subject": "",
        "headline": ("This build leans on " + "; ".join(pairings) + ".") if pairings
        else "No paired themes here — the builder picked on raw card quality.",
        "themes": themes[:6],
        "top": [],
        "drags": [],
    }


def _deck_payload(pairs: list, *, colors: set, pretty: str, decklist: str,
                  fmt: str, archetype: str, sideboard: list | None = None,
                  commander=None, target_bracket: int | None = None) -> dict:
    """pairs: list of (OwnedCard, count) for the main deck."""
    if commander is not None:
        synergy, reasons_by_name = _commander_synergy(pairs, commander)
    else:
        synergy, reasons_by_name = _standard_synergy(pairs), None

    grouped: dict[str, list] = {}
    for card, count in pairs:
        reasons = reasons_by_name.get(card.name) if reasons_by_name is not None else None
        grouped.setdefault(_category(card), []).append(_card_json(card, count, reasons))
    for cards in grouped.values():
        cards.sort(key=lambda c: (c["is_filler"], c["cmc"], c["name"]))

    categories = [
        {"name": name, "count": sum(c["count"] for c in grouped[name]), "cards": grouped[name]}
        for name in CATEGORY_ORDER if name in grouped
    ]
    # The local half of the bracket — instant, and correct offline. The combo
    # criterion needs Commander Spellbook, so /bracket refines this afterwards
    # rather than making every build wait on a third-party round trip.
    bracket = None
    if commander is not None and formats.get(fmt).singleton:
        deck_cards = [card for card, _ in pairs]
        bracket = brackets.evaluate(
            commander, deck_cards, None, fmt=fmt, target=target_bracket,
            crispi=crispi.evaluate(commander, deck_cards, None),
        )

    total = sum(count for _, count in pairs) + (1 if commander else 0)
    side = [_card_json(card, count) for card, count in (sideboard or [])]
    price = sum((card.price_usd or 0.0) * count for card, count in pairs)
    price += sum((card.price_usd or 0.0) * count for card, count in (sideboard or []))
    if commander is not None:
        price += commander.price_usd or 0.0
    return {
        "commander": _card_json(commander, 1) if commander else None,
        "format": fmt,
        "format_label": formats.get(fmt).label,
        "archetype": archetype,
        "colors": [c for c in "WUBRG" if c in colors] or ["C"],
        "categories": categories,
        "sideboard": side,
        "sideboard_total": sum(count for _, count in (sideboard or [])),
        "total": total,
        "price": round(price, 2),
        "curve": _curve(pairs),
        "pips": _pip_demand(pairs),
        "synergy": synergy,
        "bracket": bracket,
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
        "legal": {
            key: sum(1 for c in owned if c.legalities.get(spec.key) == "legal")
            for key, spec in formats.FORMATS.items()
        },
        "formats": [
            {"key": key, "label": spec.label, "singleton": spec.singleton}
            for key, spec in formats.FORMATS.items()
        ],
        "value": round(sum((c.price_usd or 0.0) * c.quantity for c in owned), 2),
    }


@app.get("/api/collection/{sid}/commanders")
def list_commanders(sid: str, limit: int = 24, edhrec: bool = False, fmt: str = "commander"):
    session = _session(sid)
    _singleton_spec(fmt)
    cache_key = f"commanders:{fmt}:{'edhrec' if edhrec else 'fast'}"
    ranked = session.get(cache_key)
    if ranked is None:
        ranked = find_commanders(session["owned"], use_popularity=edhrec, fmt=fmt)
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
    fmt: str = "commander"
    # None builds the strongest deck it can; 1-5 builds to that bracket.
    target_bracket: int | None = Field(default=None, ge=1, le=5)


def _singleton_spec(fmt: str):
    try:
        spec = formats.get(fmt)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not spec.singleton:
        raise HTTPException(400, f"{spec.label} has no commander.")
    return spec


def _constructed_spec(fmt: str):
    try:
        spec = formats.get(fmt)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if spec.singleton:
        raise HTTPException(400, f"{spec.label} needs a commander — use the commander route.")
    return spec


@app.post("/api/collection/{sid}/deck/commander")
def build_commander_deck(sid: str, req: CommanderDeckRequest):
    session = _session(sid)
    spec = _singleton_spec(req.fmt)
    owned = session["owned"]

    if req.commander_name:
        commander = next((c for c in owned if c.name == req.commander_name), None)
        if commander is None:
            raise HTTPException(404, f"{req.commander_name} isn't in this collection.")
    else:
        cache_key = f"commanders:{req.fmt}:fast"
        ranked = session.get(cache_key) or find_commanders(owned, use_popularity=False,
                                                           fmt=req.fmt)
        session[cache_key] = ranked
        if not ranked:
            raise HTTPException(422, f"No {spec.label}-legal legendary creatures in this collection.")
        commander = ranked[0][0]

    deck = build_deck(commander, owned, fmt=req.fmt, target_bracket=req.target_bracket)
    # Kept so /recommendations can diff EDHREC's list against what was actually built.
    session.setdefault("decks", {})[commander.name] = deck
    # ...and so /bracket can re-report against the same target once combos land.
    session.setdefault("targets", {})[commander.name] = req.target_bracket
    name = commander_archetype(deck, commander)

    pairs = [(card, 1) for card in deck]
    return _deck_payload(
        pairs,
        colors=set(commander.color_identity),
        pretty=format_deck(commander, deck, format_label=spec.label, name=name),
        decklist=format_decklist(commander, deck),
        fmt=req.fmt,
        archetype=name,
        commander=commander,
        target_bracket=req.target_bracket,
    )


class StandardDeckRequest(BaseModel):
    colors: str | None = None
    fmt: str = "standard"


@app.post("/api/collection/{sid}/deck/standard")
def build_standard(sid: str, req: StandardDeckRequest):
    session = _session(sid)
    spec = _constructed_spec(req.fmt)
    forced = set(req.colors.upper()) if req.colors else None
    if forced and not forced.issubset(set("WUBRG")):
        raise HTTPException(400, "Colors must be any of W, U, B, R, G.")

    try:
        entries, colors, sideboard = build_standard_deck(
            session["owned"], fmt=req.fmt, colors=forced
        )
    except Exception as exc:
        raise HTTPException(422, f"Could not build a {spec.label} deck: {exc}")
    if not entries:
        raise HTTPException(422, f"Not enough {spec.label}-legal cards in this collection.")

    name = constructed_archetype(entries, colors)
    pairs = [(e.card, e.count) for e in entries]
    return _deck_payload(
        pairs,
        colors=colors,
        pretty=format_standard_deck(entries, colors, sideboard,
                                    format_label=spec.label, name=name),
        decklist=format_standard_decklist(entries, sideboard),
        fmt=req.fmt,
        archetype=name,
        sideboard=[(e.card, e.count) for e in sideboard],
    )


# ── Bracket ──────────────────────────────────────────────────────────────────

@app.get("/api/collection/{sid}/bracket")
def bracket(sid: str, commander: str, fmt: str = "commander", refresh: bool = False):
    """The deck's bracket with the combo criterion filled in.

    The deck payload already carries a bracket computed without combo data;
    this recomputes it once Commander Spellbook has answered, which can move a
    deck up but never down.
    """
    session = _session(sid)
    deck = (session.get("decks") or {}).get(commander)
    if deck is None:
        raise HTTPException(409, "Build the deck first — the bracket describes it.")

    commander_card = next((c for c in session["owned"] if c.name == commander), None)
    if commander_card is None:
        raise HTTPException(404, f"{commander} isn't in this collection.")

    found = combos.find_combos(commander, deck, refresh=refresh)
    # The target came in on the build request, so it is read back from the
    # session rather than re-sent: the answer must be about the deck that was
    # actually built, not whatever the picker happens to say now.
    target = (session.get("targets") or {}).get(commander)
    return brackets.evaluate(commander_card, deck, found, fmt=fmt, target=target,
                             crispi=crispi.evaluate(commander_card, deck, found))


# ── EDHREC recommendations ───────────────────────────────────────────────────

def _rec_json(rec) -> dict:
    """Serialize a Recommendation; fill card details from the collection or Scryfall."""
    card = rec.owned
    data = load_by_name().get(name_key(rec.name)) if card is None else None
    # CardView stores image_uris as the "normal" URL string, not a dict.
    image_url = data.get("image_uris", "") if data is not None else ""
    return {
        "name": rec.name,
        "category": rec.category,
        "role": rec.role,
        "synergy": round(rec.synergy, 3),
        "inclusion": round(rec.inclusion, 3),
        "num_decks": rec.num_decks,
        "owned": card is not None,
        "price": round(rec.price_usd or 0.0, 2),
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

    result = recommend(commander_card, session["owned"], deck, limit=limit,
                       refresh=refresh, card_index=load_by_name())
    acquire = [_rec_json(r) for r in result["acquire"]]
    return {
        "commander": commander,
        "error": result.get("error", ""),
        "in_deck": result["in_deck"],
        "total": result["total"],
        "upgrades": [_rec_json(r) for r in result["upgrades"]],
        "acquire": acquire,
        "acquire_price": round(sum(r["price"] for r in acquire), 2),
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
